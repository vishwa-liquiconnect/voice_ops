"""
Background Job: Process Call Recording

Per-question pipeline:
1. For each response row with a recording_url:
   a. Download recording from Twilio
   b. Send to Sarvam AI for transcription
   c. Normalize the response
2. Evaluate business rules
3. Route to review queue if needed

Per CLAUDE.md: All external calls include retry logic. No silent failures.
"""

import frappe

from voice_ops.jobs.call_log_handler import download_and_attach_recording, _download_recording
from voice_ops.services.escalation import process_escalations
from voice_ops.services.rule_evaluator import evaluate_checklist
from voice_ops.services.sarvam import transcribe_bytes
from voice_ops.services.transcript_processor import normalize_response, _estimate_segment_confidence


def process(twilio_log_name=None, recording_url=None, checklist_run_name=None):
	"""Main background job entry point."""
	try:
		if not checklist_run_name:
			if twilio_log_name:
				checklist_run_name = frappe.db.get_value(
					"Twilio Call Log", twilio_log_name, "reference_name"
				)
			if not checklist_run_name:
				return

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		checklist_run.status = "Processing"
		checklist_run.save(ignore_permissions=True)
		frappe.db.commit()

		template = frappe.get_doc("Checklist Template", checklist_run.checklist_template)
		language_code = template.language or "hi-IN"

		# Process each response row that has a recording
		for response in checklist_run.responses:
			if not response.recording_url:
				# Question not answered (driver hung up early)
				continue

			try:
				_process_single_response(response, language_code, twilio_log_name)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Voice Ops: Failed processing question {response.question_key}",
				)

		checklist_run.save(ignore_permissions=True)
		frappe.db.commit()

		# Evaluate business rules
		evaluation = evaluate_checklist(checklist_run_name)

		# Process escalations if any triggers were found
		if evaluation.get("escalation_triggers"):
			process_escalations(checklist_run_name, evaluation["escalation_triggers"])

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Failed processing checklist {checklist_run_name}",
		)
		if checklist_run_name:
			frappe.db.set_value("Checklist Run", checklist_run_name, "status", "Call Completed")
			frappe.db.commit()


def _process_single_response(response, language_code, twilio_log_name=None):
	"""Download, transcribe, and normalize a single question's recording."""
	audio_bytes = _download_recording(response.recording_url)
	if not audio_bytes or len(audio_bytes) < 100:
		response.raw_transcript = ""
		response.normalized_response = ""
		response.confidence = 0.0
		return

	file_name = f"recording_{response.question_key}.mp3"
	sarvam_result = transcribe_bytes(audio_bytes, file_name=file_name)

	transcript = sarvam_result.get("transcript", "")
	detected_language = sarvam_result.get("language_code") or language_code

	response.raw_transcript = transcript

	# Normalize based on response type
	response_type = response.response_type or "Yes/No"
	normalized = normalize_response(transcript, response_type, detected_language)
	response.normalized_response = str(normalized) if normalized is not None else ""
	response.confidence = _estimate_segment_confidence(transcript, normalized)

	# Attach recording to S3 for archival (non-blocking, best effort)
	if twilio_log_name:
		try:
			download_and_attach_recording(twilio_log_name, response.recording_url, audio_bytes=audio_bytes)
		except Exception:
			pass
