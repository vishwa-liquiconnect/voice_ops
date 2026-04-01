"""
Auto-trigger Post-Trip Checklist Calls

Scheduler job that runs every 5 minutes. Finds Trip Roster Assignments
where the trip has been completed (status changed to a terminal state)
and triggers a post-arrival checklist call to driver_1.

Per CLAUDE.md: ERPNext triggers calls via Exotel. All business decisions
are deterministic. No silent failures.
"""

import frappe
from frappe.utils import now_datetime, add_to_date, today, getdate

from voice_ops.services.exotel import initiate_call


def check_and_trigger_post_trip():
	"""
	Main scheduler entry point. Runs every 5 minutes.

	Finds completed Trip Roster Assignments that need a post-trip
	checklist call and triggers them.
	"""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	template_name = settings.post_trip_checklist_template

	if not template_name:
		# Post-trip not configured — silently skip
		return

	buffer_minutes = settings.post_trip_buffer_minutes or 30
	now = now_datetime()
	current_date = getdate(today())

	# Find Trip Roster Assignments that completed today
	# "Completed" status indicates the trip has finished
	assignments = frappe.db.sql("""
		SELECT
			tra.name,
			tra.date,
			tra.driver_1,
			tra.driver_1_mobile_number,
			tra.vehicle,
			tra.modified
		FROM `tabTrip Roster Assignment` tra
		WHERE
			tra.date = %s
			AND tra.status = 'Completed'
			AND tra.docstatus = 1
			AND tra.driver_1 IS NOT NULL
			AND tra.driver_1 != ''
			AND tra.driver_1_mobile_number IS NOT NULL
			AND tra.driver_1_mobile_number != ''
		LIMIT 50
	""", (current_date,), as_dict=True)

	for assignment in assignments:
		try:
			_process_post_trip(assignment, buffer_minutes, template_name, now)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Failed post-trip trigger for {assignment.get('name')}",
			)


def _process_post_trip(assignment, buffer_minutes, template_name, now):
	"""Check if a completed assignment needs a post-trip call and trigger it."""
	# Only trigger within buffer_minutes of completion
	modified_dt = assignment.get("modified")
	if modified_dt:
		trigger_deadline = add_to_date(modified_dt, minutes=buffer_minutes)
		if now > trigger_deadline:
			return

	# Skip if a post-trip Checklist Run already exists for this assignment
	existing = frappe.db.sql("""
		SELECT cr.name
		FROM `tabChecklist Run` cr
		INNER JOIN `tabChecklist Template` ct ON cr.checklist_template = ct.name
		WHERE
			cr.trip_roster_assignment = %s
			AND ct.checklist_type = 'Post-Arrival'
		LIMIT 1
	""", (assignment["name"],), as_dict=True)

	if existing:
		return

	_create_and_trigger(assignment, template_name)


def _create_and_trigger(assignment, template_name):
	"""Create a post-trip Checklist Run and initiate the call."""
	run = frappe.get_doc({
		"doctype": "Checklist Run",
		"checklist_template": template_name,
		"trip_roster_assignment": assignment["name"],
		"crew_member": assignment["driver_1"],
		"mobile_number": assignment["driver_1_mobile_number"],
		"vehicle": assignment.get("vehicle"),
		"status": "Draft",
	})
	run.insert(ignore_permissions=True)

	run.populate_responses_from_template()
	run.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		call_log_name = initiate_call(
			to_number=assignment["driver_1_mobile_number"],
			reference_doctype="Checklist Run",
			reference_name=run.name,
		)

		run.call_log = call_log_name
		run.status = "Call Initiated"
		run.initiated_at = now_datetime()
		run.save(ignore_permissions=True)
		frappe.db.commit()

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Post-trip call initiation failed for {run.name}",
		)
