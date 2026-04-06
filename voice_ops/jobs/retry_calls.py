"""
Background Job: Retry Failed Calls

Scheduler job that finds Checklist Runs where the call failed
and re-initiates them if attempts remain.

Per CLAUDE.md: Failed jobs must be logged and retried.
"""

import frappe
from frappe.utils import now_datetime

from voice_ops.services.telephony import get_provider
from voice_ops.services.twilio_service import initiate_call


def process_pending_retries():
	"""
	Find Checklist Runs with failed calls and retry them.

	Runs on a cron schedule (every 5 minutes).
	"""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	max_attempts = settings.max_call_attempts or 3

	# Query differs by provider due to different call log doctypes
	provider = get_provider()
	if provider == "Exotel":
		failed_runs = frappe.db.sql("""
			SELECT
				cr.name AS checklist_run,
				cr.mobile_number,
				cr.call_log,
				cl.status AS call_status
			FROM `tabChecklist Run` cr
			LEFT JOIN `tabCall Log` cl ON cr.call_log = cl.name
			WHERE
				cr.status = 'Call Initiated'
				AND cl.status IN ('No Answer', 'Canceled', 'Failed')
				AND cr.mobile_number IS NOT NULL
				AND cr.mobile_number != ''
			LIMIT 10
		""", as_dict=True)
	else:
		failed_runs = frappe.db.sql("""
			SELECT
				cr.name AS checklist_run,
				cr.mobile_number,
				cr.call_log,
				tcl.call_status,
				tcl.attempt_no
			FROM `tabChecklist Run` cr
			LEFT JOIN `tabTwilio Call Log` tcl ON cr.call_log = tcl.name
			WHERE
				cr.status = 'Call Initiated'
				AND tcl.call_status IN ('no-answer', 'busy', 'failed', 'canceled', 'max_attempts_reached')
				AND cr.mobile_number IS NOT NULL
				AND cr.mobile_number != ''
			LIMIT 10
		""", as_dict=True)

	for run in failed_runs:
		attempt_count = run.get("attempt_no") or 1
		if attempt_count >= max_attempts:
			frappe.db.set_value("Checklist Run", run["checklist_run"], {
				"status": "Draft",
				"evaluation_notes": f"Call failed after {attempt_count} attempts",
			})
			frappe.db.commit()
			continue

		try:
			_retry_call(run)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Failed retrying call for {run['checklist_run']}",
			)


def _retry_call(run):
	"""Retry a failed call for a Checklist Run."""
	twilio_log_name = initiate_call(
		to_number=run["mobile_number"],
		reference_doctype="Checklist Run",
		reference_name=run["checklist_run"],
	)

	frappe.db.set_value("Checklist Run", run["checklist_run"], {
		"call_log": twilio_log_name,
		"status": "Call Initiated",
		"initiated_at": now_datetime(),
	})
	frappe.db.commit()
