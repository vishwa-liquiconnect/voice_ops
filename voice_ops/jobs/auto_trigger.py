"""
Auto-trigger Checklist Calls

Scheduler job that runs every 5 minutes. Finds Trip Roster Assignments
where the scheduled departure is within the buffer window and triggers
a pre-departure checklist call to driver_1.

Per CLAUDE.md: ERPNext triggers calls via Twilio. All business decisions
are deterministic. No silent failures.
"""

import frappe
from frappe.utils import now_datetime, get_datetime, add_to_date, today, getdate

from voice_ops.services.twilio_service import initiate_call


def check_and_trigger():
	"""
	Main scheduler entry point. Runs every 5 minutes.

	Finds Trip Roster Assignments due for a checklist call and triggers them.
	"""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	buffer_minutes = settings.call_buffer_minutes or 30
	template_name = settings.default_checklist_template

	if not template_name:
		frappe.log_error(
			"Voice Ops: No default checklist template configured",
			"Voice Ops: Auto-trigger Skipped",
		)
		return

	now = now_datetime()
	current_date = getdate(today())

	# Find eligible Trip Roster Assignments:
	# - date is today
	# - status is "Not Started"
	# - driver_1 and driver_1_mobile_number are set
	# - docstatus = 1 (submitted)
	assignments = frappe.db.sql("""
		SELECT
			tra.name,
			tra.date,
			tra.driver_1,
			tra.driver_1_mobile_number,
			tra.vehicle,
			tra.schedule,
			sa.select_timings
		FROM `tabTrip Roster Assignment` tra
		INNER JOIN `tabSchedule Addition` sa ON tra.schedule = sa.name
		WHERE
			tra.date = %s
			AND tra.status = 'Not Started'
			AND tra.docstatus = 1
			AND tra.driver_1 IS NOT NULL
			AND tra.driver_1 != ''
			AND tra.driver_1_mobile_number IS NOT NULL
			AND tra.driver_1_mobile_number != ''
			AND sa.select_timings IS NOT NULL
	""", (current_date,), as_dict=True)

	for assignment in assignments:
		try:
			_process_assignment(assignment, buffer_minutes, template_name, now)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Failed to process {assignment.get('name')}",
			)


def _process_assignment(assignment, buffer_minutes, template_name, now):
	"""Check if a single assignment is due for a call and trigger it."""
	# Compute departure datetime from date + select_timings
	departure_dt = _compute_departure_datetime(assignment)
	if not departure_dt:
		return

	# Compute trigger window
	trigger_time = add_to_date(departure_dt, minutes=-buffer_minutes)

	# Only trigger if we're within the window: trigger_time <= now < departure_dt
	if not (trigger_time <= now < departure_dt):
		return

	# Skip if a Checklist Run already exists for this assignment
	existing = frappe.db.exists(
		"Checklist Run",
		{"trip_roster_assignment": assignment["name"]},
	)
	if existing:
		return

	# Create Checklist Run and trigger call
	_create_and_trigger(assignment, template_name)


def _compute_departure_datetime(assignment):
	"""
	Compute departure datetime from Trip Roster Assignment date
	and Schedule Addition's select_timings.

	Returns a datetime object or None.
	"""
	trip_date = assignment.get("date")
	select_timings = assignment.get("select_timings")

	if not trip_date or not select_timings:
		return None

	try:
		# select_timings is a timedelta from midnight (Frappe Time field)
		# Convert date + time to datetime
		from datetime import datetime, timedelta

		if isinstance(select_timings, timedelta):
			hours = int(select_timings.total_seconds() // 3600)
			minutes = int((select_timings.total_seconds() % 3600) // 60)
		else:
			# Could be a string like "04:00:00"
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


def _create_and_trigger(assignment, template_name):
	"""Create a Checklist Run and initiate the call."""
	# Create the Checklist Run
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
	frappe.db.commit()

	# Initiate call via Twilio
	try:
		call_log_name = initiate_call(
			to_number=assignment["driver_1_mobile_number"],
			reference_doctype="Checklist Run",
			reference_name=run.name,
		)

		# Link Call Log to the Checklist Run
		run.call_log = call_log_name
		run.status = "Call Initiated"
		run.initiated_at = now_datetime()
		run.save(ignore_permissions=True)
		frappe.db.commit()

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Call initiation failed for {run.name}",
		)
		# Run stays in Draft status — retry job will pick it up
