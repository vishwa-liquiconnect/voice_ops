"""
Call Recording Handler

Downloads recordings and attaches them as Files
(auto-uploaded to S3 via frappe_s3_attachment).

Handles both Twilio Call Log and Exotel Call Log on_update events
to detect mid-call hangups and trigger processing for partial checklists.
"""

import frappe

from voice_ops.services.telephony import download_and_attach_recording, download_recording


def on_twilio_call_log_update(doc, method):
	"""
	Called on Twilio Call Log on_update (via doc_events hook).

	When a call completes (or fails), check if the linked Checklist Run
	is stuck at "Call Initiated". If so, move it to "Call Completed"
	and enqueue processing for whatever recordings were captured.
	"""
	if doc.type != "Call":
		return

	if doc.call_status not in ("completed", "no-answer", "busy", "failed", "canceled"):
		return

	if not doc.reference_doctype == "Checklist Run" or not doc.reference_name:
		return

	_handle_call_completion(doc.reference_name, doc.name)


def fix_exotel_null_status(doc, method):
	"""Fix Exotel sending the string 'null' as DialCallStatus for voicemail calls.
	Also capture recording URL from the webhook payload since exotel_integration
	only sets it during update_call_log, not create_call_log.
	"""
	if doc.status == "null":
		doc.status = "No Answer"

	if not doc.recording_url and frappe.form_dict.get("RecordingUrl"):
		doc.recording_url = frappe.form_dict.get("RecordingUrl")

	# Tag voicemail calls from Exotel webhook payload
	if frappe.form_dict.get("CallType") == "voicemail":
		if frappe.db.exists("Telephony Call Type", "Voicemail"):
			doc.type_of_call = "Voicemail"


def attach_exotel_recording(doc, method):
	"""Download Exotel recording and attach as a file so it's playable from ERPNext.

	Dedup note: this hook fires on both after_insert and on_update so that
	outbound calls — whose recording_url arrives via a later webhook — get
	processed too. That means inbound voicemails, which hit both hooks in
	quick succession, would otherwise enqueue two download jobs. The cache
	key below serialises the enqueue across both firings within a 10-min
	window.
	"""
	if not doc.recording_url or "exotel.com" not in doc.recording_url:
		return

	if frappe.db.exists("File", {"attached_to_doctype": "Call Log", "attached_to_name": doc.name, "file_name": ("like", "call_recording_%")}):
		return

	cache_key = f"voice_ops:recording_enqueued:{doc.name}"
	if frappe.cache().get_value(cache_key):
		return
	frappe.cache().set_value(cache_key, 1, expires_in_sec=600)

	frappe.enqueue(
		"voice_ops.jobs.call_log_handler._download_and_attach_exotel_recording",
		queue="short",
		call_log_name=doc.name,
		recording_url=doc.recording_url,
	)


def _download_and_attach_exotel_recording(call_log_name, recording_url):
	from voice_ops.services.telephony import _download_exotel_recording, download_and_attach_recording

	audio = _download_exotel_recording(recording_url)
	if audio:
		download_and_attach_recording(call_log_name, recording_url, audio_bytes=audio)
		_transcribe_voicemail(call_log_name, audio, recording_url)


def _transcribe_voicemail(call_log_name, audio_bytes, recording_url):
	"""Transcribe a Voicemail or Feedback recording and store the raw transcript in `summary`.

	Voicemail flow also resolves caller links and enqueues issue creation.
	Feedback flow stops after transcription — the Call Log already has the
	outbound reference links from initiate_feedback_call, and feedback is
	not an actionable issue.

	Claude summarization (where applicable) is deferred to downstream jobs
	so it only runs at alert/digest time, not per call.
	"""
	type_of_call = frappe.db.get_value("Call Log", call_log_name, "type_of_call")
	if type_of_call not in ("Voicemail", "Feedback"):
		return

	from voice_ops.services.sarvam import transcribe_bytes

	file_name = f"{type_of_call.lower()}.mp3" if ".mp3" in recording_url else f"{type_of_call.lower()}.wav"
	try:
		result = transcribe_bytes(audio_bytes, file_name=file_name)
		transcript = (result.get("transcript") or "").strip()
		if not transcript:
			return

		updates = {"summary": transcript}
		language_code = (result.get("language_code") or "").strip()
		if language_code:
			updates["custom_detected_language"] = language_code
		frappe.db.set_value("Call Log", call_log_name, updates)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: {type_of_call} transcription failed for {call_log_name}",
		)
		return

	# Voicemail is inbound (`from` is the caller) so resolve caller links.
	# Feedback is outbound (`from` is our exophone) so skip the lookup —
	# the Call Log already links to the reference doctype passed at
	# initiation (e.g. Trip Roster Assignment). The Issue pipeline itself
	# decides whether to create an Issue via the is_actionable gate.
	if type_of_call == "Voicemail":
		_attach_caller_links(call_log_name)

	frappe.enqueue(
		"voice_ops.jobs.create_call_log_issue.run",
		queue="long",
		call_log_name=call_log_name,
	)


def _attach_caller_links(call_log_name):
	"""Resolve the caller once at transcription time and attach Contact
	/ Employee / Vehicle rows to `Call Log.links`. Downstream consumers
	read from the links instead of re-resolving."""
	try:
		from voice_ops.services.caller_lookup import resolve_caller
		from voice_ops.services.call_log_links import add_call_log_links

		from_phone = frappe.db.get_value("Call Log", call_log_name, "from")
		info = resolve_caller(from_phone) or {}
		add_call_log_links(
			call_log_name,
			[
				("Contact", info.get("contact")),
				("Employee", info.get("employee")),
				("Vehicle", info.get("vehicle")),
			],
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: caller link attach failed for {call_log_name}",
		)


def on_exotel_call_log_update(doc, method):
	"""
	Called on Call Log (Exotel) on_update (via doc_events hook).

	Same logic as Twilio handler but for Exotel's Call Log doctype.
	"""
	if doc.status not in ("Completed", "No Answer", "Canceled", "Failed"):
		return

	# Exotel Call Log uses 'links' child table for references
	checklist_run_name = None
	if hasattr(doc, "links"):
		for link in doc.links:
			if link.link_doctype == "Checklist Run":
				checklist_run_name = link.link_name
				break

	if not checklist_run_name:
		return

	_handle_call_completion(checklist_run_name, doc.name)


def _handle_call_completion(checklist_run_name, call_log_name):
	"""Common handler for call completion from either provider."""
	if not frappe.db.exists("Checklist Run", checklist_run_name):
		return

	current_status = frappe.db.get_value("Checklist Run", checklist_run_name, "status")

	# Only act if stuck at Call Initiated (mid-call hangup)
	if current_status != "Call Initiated":
		return

	frappe.db.set_value("Checklist Run", checklist_run_name, {
		"status": "Call Completed",
		"completed_at": frappe.utils.now_datetime(),
	})

	# Enqueue processing — it will handle whatever recordings exist
	frappe.enqueue(
		"voice_ops.jobs.process_call_recording.process",
		queue="long",
		twilio_log_name=call_log_name,
		checklist_run_name=checklist_run_name,
	)
	frappe.db.commit()
