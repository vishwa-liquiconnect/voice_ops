"""
Background Job: Retry Failed Calls

Scheduler job that finds Checklist Runs where the call failed
and re-initiates them if attempts remain.

Per CLAUDE.md: Failed jobs must be logged and retried.
"""

import frappe
from frappe.utils import now_datetime

from voice_ops.services.exotel import initiate_call


def process_pending_retries():
	"""
	Find Checklist Runs with failed calls and retry them.

	Runs on a cron schedule (every 5 minutes).
	"""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	max_attempts = settings.max_call_attempts or 3

	# Find Checklist Runs that are in "Call Initiated" status
	# but whose linked Call Log has a terminal failure status
	failed_runs = frappe.db.sql("""
		SELECT
			cr.name AS checklist_run,
			cr.mobile_number,
			cr.call_log,
			cr.trip_roster_assignment,
			cl.status AS call_status,
			COALESCE(
				(SELECT COUNT(*) FROM `tabCall Log` cl2
				 INNER JOIN `tabDynamic Link` dl ON dl.parent = cl2.name
				 WHERE dl.link_doctype = 'Checklist Run'
				 AND dl.link_name = cr.name
				 AND dl.parenttype = 'Call Log'), 0
			) AS attempt_count
		FROM `tabChecklist Run` cr
		LEFT JOIN `tabCall Log` cl ON cr.call_log = cl.name
		WHERE
			cr.status = 'Call Initiated'
			AND cl.status IN ('Failed', 'No Answer', 'Canceled')
			AND cr.mobile_number IS NOT NULL
			AND cr.mobile_number != ''
		LIMIT 10
	""", as_dict=True)

	for run in failed_runs:
		if (run.get("attempt_count") or 0) >= max_attempts:
			# Max attempts reached — mark as failed
			frappe.db.set_value("Checklist Run", run["checklist_run"], {
				"status": "Draft",
				"evaluation_notes": f"Call failed after {run['attempt_count']} attempts",
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
	call_log_name = initiate_call(
		to_number=run["mobile_number"],
		reference_doctype="Checklist Run",
		reference_name=run["checklist_run"],
	)

	frappe.db.set_value("Checklist Run", run["checklist_run"], {
		"call_log": call_log_name,
		"status": "Call Initiated",
		"initiated_at": now_datetime(),
	})
	frappe.db.commit()
