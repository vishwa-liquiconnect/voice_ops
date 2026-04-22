"""
Auto-trigger Post-Trip Feedback Calls

Scheduler job that runs every 5 minutes. Finds Trip Roster Assignments
whose trip has ended more than `feedback_buffer_minutes` ago and
triggers a single feedback call to driver_1 via the Exotel feedback
flow.

Trip end is derived from the linked Schedule Addition's
`schedule_locations` child table — specifically the row whose
`point_type = 'End'`. Day offset on that row (Day 1 / 2 / 3) handles
multi-day trips. We widen the TRA date filter to the last 7 days so a
trip that started on Monday but ends today still qualifies.

Dedup: one feedback call per Trip Roster Assignment. Checked by
looking for an existing Call Log with type_of_call = 'Feedback'
linked to the TRA via Dynamic Link.
"""

from datetime import datetime, timedelta

import frappe
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime, today

from voice_ops.services.feedback_call import initiate_feedback_call


# TRAs beyond this many days ago aren't considered — avoids scanning
# the full history on every tick. Tune up for long-haul fleets.
LOOKBACK_DAYS = 7


def check_and_trigger():
	"""Scheduler entry point. Runs every 5 minutes."""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	if not frappe.db.get_single_value("Voice Ops Settings", "enable_feedback_auto_call"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	buffer_minutes = settings.feedback_buffer_minutes
	if buffer_minutes is None:
		buffer_minutes = 30

	now = now_datetime()
	current_date = getdate(today())
	from_date = add_to_date(current_date, days=-LOOKBACK_DAYS)

	assignments = frappe.db.sql("""
		SELECT
			tra.name,
			tra.date,
			tra.driver_1,
			tra.driver_1_mobile_number,
			tra.status,
			tra.schedule
		FROM `tabTrip Roster Assignment` tra
		WHERE
			tra.date BETWEEN %s AND %s
			AND tra.docstatus = 1
			AND IFNULL(tra.status, '') != 'Cancelled'
			AND tra.driver_1 IS NOT NULL
			AND tra.driver_1 != ''
			AND tra.driver_1_mobile_number IS NOT NULL
			AND tra.driver_1_mobile_number != ''
			AND tra.schedule IS NOT NULL
			AND tra.schedule != ''
	""", (from_date, current_date), as_dict=True)

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
	end_dt = _compute_trip_end_datetime(assignment["schedule"], assignment["date"])
	if not end_dt:
		return

	trigger_time = add_to_date(end_dt, minutes=buffer_minutes)
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


def _compute_trip_end_datetime(schedule_name, trip_date):
	"""Return the absolute trip-end datetime from the Schedule Addition's
	`schedule_locations` child table.

	Strategy: take the last stop in the schedule — highest idx with a
	non-null time. This works for schedules that use explicit
	'Start'/'End' markers AND for schedules that use 'Boarding Point'/
	'Dropping Point' vocab (the common case), where the final dropping
	point is the trip end. Day field on that row carries the offset for
	multi-day routes."""
	if not schedule_name or not trip_date:
		return None

	rows = frappe.db.sql("""
		SELECT day, time
		FROM `tabSchedule Locations`
		WHERE parent = %s
			AND parenttype = 'Schedule Addition'
			AND parentfield = 'schedule_locations'
			AND time IS NOT NULL
		ORDER BY idx DESC
		LIMIT 1
	""", (schedule_name,), as_dict=True)

	if not rows:
		return None

	row = rows[0]
	raw_time = row.get("time")
	if raw_time is None:
		return None

	try:
		if isinstance(raw_time, timedelta):
			hours = int(raw_time.total_seconds() // 3600)
			minutes = int((raw_time.total_seconds() % 3600) // 60)
		else:
			parts = str(raw_time).split(":")
			hours = int(parts[0])
			minutes = int(parts[1]) if len(parts) > 1 else 0

		day_num = _parse_day(row.get("day"))
		end_date = trip_date + timedelta(days=day_num - 1)
		end_dt = datetime(
			year=end_date.year,
			month=end_date.month,
			day=end_date.day,
			hour=hours,
			minute=minutes,
		)
		return get_datetime(end_dt)
	except Exception:
		frappe.log_error(
			f"Voice Ops: Cannot compute trip end for schedule={schedule_name}, "
			f"date={trip_date}, day={row.get('day')}, time={raw_time}",
			"Voice Ops: Invalid Trip End Time",
		)
		return None


def _parse_day(raw):
	"""Return the day number from a 'Day N' string (default 1)."""
	if not raw:
		return 1
	digits = "".join(ch for ch in str(raw) if ch.isdigit())
	try:
		return int(digits) if digits else 1
	except Exception:
		return 1
