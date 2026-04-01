"""
Background Job: Process Call Recording

Pipeline:
1. Download recording from Exotel → attach as File (auto S3 via frappe_s3_attachment)
2. Send file to Sarvam AI speech-to-text-translate → get transcript
3. Process transcript against template questions
4. Evaluate business rules
5. Route to review queue if needed

Per CLAUDE.md: All external calls include retry logic. No silent failures.
"""

import json

import frappe

from voice_ops.jobs.call_log_handler import download_and_attach_recording
from voice_ops.services.escalation import process_escalations
from voice_ops.services.rule_evaluator import evaluate_checklist
from voice_ops.services.sarvam import transcribe_from_attachment
from voice_ops.services.transcript_processor import process_transcript


def process(call_log_name, recording_url, checklist_run_name=None):
	"""Main background job entry point."""
	try:
		call_log = frappe.get_doc("Call Log", call_log_name)

		if not checklist_run_name:
			checklist_run_name = _find_checklist_run(call_log)

		if not checklist_run_name:
			return

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		checklist_run.status = "Processing"
		checklist_run.save(ignore_permissions=True)
		frappe.db.commit()

		# Step 1: Download recording and attach as File (auto goes to S3)
		file_doc_name = download_and_attach_recording(call_log_name, recording_url)
		if not file_doc_name:
			frappe.log_error(
				f"Failed to download recording for Call Log {call_log_name}",
				"Voice Ops: Recording Download Failed",
			)
			checklist_run.status = "Call Completed"
			checklist_run.save(ignore_permissions=True)
			frappe.db.commit()
			return

		# Step 2: Send file to Sarvam AI for transcription
		sarvam_result = transcribe_from_attachment(file_doc_name)

		if not sarvam_result.get("transcript"):
			frappe.log_error(
				f"Sarvam returned empty transcript for {call_log_name}: {sarvam_result}",
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
			f"Voice Ops: Failed processing call {call_log_name}",
		)
		if checklist_run_name:
			frappe.db.set_value("Checklist Run", checklist_run_name, "status", "Call Completed")
			frappe.db.commit()


def _find_checklist_run(call_log):
	"""Find the Checklist Run linked to a Call Log."""
	for link in call_log.get("links", []):
		if link.link_doctype == "Checklist Run" and link.link_name:
			if frappe.db.exists("Checklist Run", link.link_name):
				return link.link_name

	return frappe.db.get_value("Checklist Run", {"call_log": call_log.name}, "name")
