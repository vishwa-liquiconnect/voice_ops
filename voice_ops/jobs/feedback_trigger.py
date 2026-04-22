"""
Auto-trigger Post-Trip Feedback Calls

Scheduler job that runs every 5 minutes. Finds Trip Roster Assignments
where the scheduled departure time was more than `feedback_buffer_minutes`
ago and triggers a single feedback call to driver_1 via the Exotel
feedback flow.

Mirrors the time-based pattern used in auto_trigger.py (pre-departure)
and post_trip_trigger.py (post-completion), but the trigger moment is
`scheduled_departure + buffer` rather than tied to trip status.

Dedup: one feedback call per Trip Roster Assignment. Checked by looking
for an existing Call Log with type_of_call = 'Feedback' linked to the
TRA via Dynamic Link.
"""

from datetime import datetime, timedelta

import frappe
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime, today

from voice_ops.services.feedback_call import initiate_feedback_call


def check_and_trigger():
	"""Scheduler entry point. Runs every 5 minutes."""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	if not frappe.db.get_single_value("Voice Ops Settings", "enable_feedback_auto_call"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	buffer_minutes = settings.feedback_buffer_minutes or 30

	now = now_datetime()
	current_date = getdate(today())

	assignments = frappe.db.sql("""
		SELECT
			tra.name,
			tra.date,
			tra.driver_1,
			tra.driver_1_mobile_number,
			tra.status,
			sa.select_timings
		FROM `tabTrip Roster Assignment` tra
		INNER JOIN `tabSchedule Addition` sa ON tra.schedule = sa.name
		WHERE
			tra.date = %s
			AND tra.docstatus = 1
			AND IFNULL(tra.status, '') != 'Cancelled'
			AND tra.driver_1 IS NOT NULL
			AND tra.driver_1 != ''
			AND tra.driver_1_mobile_number IS NOT NULL
			AND tra.driver_1_mobile_number != ''
			AND sa.select_timings IS NOT NULL
	""", (current_date,), as_dict=True)

	for assignment in assignments:
		try:
			_process_assignment(assignment, buffer_minutes, now)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Feedback auto-trigger failed for {assignment.get('name')}",
			)


def _process_assignment(assignment, buffer_minutes, now):
	"""Trigger a feedback call for one assignment if eligible."""
	departure_dt = _compute_departure_datetime(assignment)
	if not departure_dt:
		return

	trigger_time = add_to_date(departure_dt, minutes=buffer_minutes)
	if now < trigger_time:
		return

	if _feedback_call_exists(assignment["name"]):
		return

	try:
		initiate_feedback_call(
			to_number=assignment["driver_1_mobile_number"],
			employee=assignment["driver_1"],
			reference_doctype="Trip Roster Assignment",
			reference_name=assignment["name"],
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Feedback call initiation failed for {assignment['name']}",
		)


def _feedback_call_exists(tra_name):
	"""True if a Feedback-type Call Log is already linked to this Trip Roster Assignment."""
	rows = frappe.db.sql("""
		SELECT cl.name
		FROM `tabCall Log` cl
		INNER JOIN `tabDynamic Link` dl ON dl.parent = cl.name
			AND dl.parenttype = 'Call Log'
			AND dl.parentfield = 'links'
		WHERE
			dl.link_doctype = 'Trip Roster Assignment'
			AND dl.link_name = %s
			AND cl.type_of_call = 'Feedback'
		LIMIT 1
	""", (tra_name,))
	return bool(rows)


def _compute_departure_datetime(assignment):
	"""Build a datetime from Trip Roster Assignment.date + Schedule Addition.select_timings."""
	trip_date = assignment.get("date")
	select_timings = assignment.get("select_timings")
	if not trip_date or not select_timings:
		return None

	try:
		if isinstance(select_timings, timedelta):
			hours = int(select_timings.total_seconds() // 3600)
			minutes = int((select_timings.total_seconds() % 3600) // 60)
		else:
			parts = str(select_timings).split(":")
			hours = int(parts[0])
			minutes = int(parts[1]) if len(parts) > 1 else 0

		departure_dt = datetime(
			year=trip_date.year,
			month=trip_date.month,
			day=trip_date.day,
			hour=hours,
			minute=minutes,
		)
		return get_datetime(departure_dt)
	except Exception:
		frappe.log_error(
			f"Voice Ops: Cannot compute departure for {assignment.get('name')}: "
			f"date={trip_date}, timings={select_timings}",
			"Voice Ops: Invalid Departure Time",
		)
		return None
