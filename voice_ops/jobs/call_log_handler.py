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
