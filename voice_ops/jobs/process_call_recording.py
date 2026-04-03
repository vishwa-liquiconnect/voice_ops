"""
Background Job: Process Call Recording

Pipeline:
1. Download recording from Twilio -> attach as File (auto S3 via frappe_s3_attachment)
2. Send file to Sarvam AI speech-to-text-translate -> get transcript
3. Process transcript against template questions
4. Evaluate business rules
5. Route to review queue if needed

Per CLAUDE.md: All external calls include retry logic. No silent failures.
"""

import frappe

from voice_ops.jobs.call_log_handler import download_and_attach_recording, _download_recording
from voice_ops.services.escalation import process_escalations
from voice_ops.services.rule_evaluator import evaluate_checklist
from voice_ops.services.sarvam import transcribe_bytes
from voice_ops.services.transcript_processor import process_transcript


def process(twilio_log_name, recording_url, checklist_run_name=None):
	"""Main background job entry point."""
	try:
		if not checklist_run_name:
			checklist_run_name = frappe.db.get_value(
				"Twilio Call Log", twilio_log_name, "reference_name"
			)

		if not checklist_run_name:
			return

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		checklist_run.status = "Processing"
		checklist_run.save(ignore_permissions=True)
		frappe.db.commit()

		# Step 1: Download recording bytes
		audio_bytes = _download_recording(recording_url)
		if not audio_bytes:
			frappe.log_error(
				f"Failed to download recording for Twilio Call Log {twilio_log_name}",
				"Voice Ops: Recording Download Failed",
			)
			checklist_run.status = "Call Completed"
			checklist_run.save(ignore_permissions=True)
			frappe.db.commit()
			return

		# Step 2: Transcribe directly from bytes (no S3 round-trip)
		file_name = f"recording_{twilio_log_name}.mp3" if ".mp3" in recording_url else f"recording_{twilio_log_name}.wav"
		sarvam_result = transcribe_bytes(audio_bytes, file_name=file_name)

		# Step 2b: Attach recording to S3 for archival (non-blocking)
		download_and_attach_recording(twilio_log_name, recording_url, audio_bytes=audio_bytes)

		if not sarvam_result.get("transcript"):
			frappe.log_error(
				f"Sarvam returned empty transcript for {twilio_log_name}: {sarvam_result}",
				"Voice Ops: Empty Transcript",
			)

		# Step 3: Get template questions
		template = frappe.get_doc("Checklist Template", checklist_run.checklist_template)
		template_questions = [
			{
				"question_key": q.question_key,
				"question_text": q.question_text,
				"expected_response_type": q.expected_response_type,
				"select_options": q.select_options,
				"is_blocker": q.is_blocker,
			}
			for q in sorted(template.questions, key=lambda x: x.sequence or 0)
		]

		# Step 4: Process transcript against questions
		processed_responses = process_transcript(
			sarvam_result["transcript"],
			template_questions,
			sarvam_result.get("language_code"),
		)

		# Step 5: Update Checklist Response rows
		for processed in processed_responses:
			for response in checklist_run.responses:
				if response.question_key == processed["question_key"]:
					response.raw_transcript = processed["raw_transcript"]
					response.normalized_response = processed["normalized_response"]
					response.confidence = processed["confidence"]
					break

		checklist_run.save(ignore_permissions=True)
		frappe.db.commit()

		# Step 6: Evaluate business rules
		evaluation = evaluate_checklist(checklist_run_name)

		# Step 7: Process escalations if any triggers were found
		if evaluation.get("escalation_triggers"):
			process_escalations(checklist_run_name, evaluation["escalation_triggers"])

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Failed processing call {twilio_log_name}",
		)
		if checklist_run_name:
			frappe.db.set_value("Checklist Run", checklist_run_name, "status", "Call Completed")
			frappe.db.commit()
