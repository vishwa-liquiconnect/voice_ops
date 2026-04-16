"""
Caller Identity Lookup

Resolves a mobile number to a caller name (via Contact or Employee) and
the most-recently-assigned vehicle (via Trip Crew Member → Trip Roster
Assignment). Used to enrich voicemail digests so operators see "Rajesh
on TN-09-AB-1234" instead of a raw phone number.
"""

import frappe


def resolve_caller(phone):
	"""Resolve a phone number to `{name, employee, vehicle_label}`.

	Runs three independent lookups (Contact, Employee, Trip Crew Member)
	and merges: prefers a Contact-side human name, but still picks up the
	Employee id from any source so the vehicle lookup can run. Returns
	None when no source matches at all.
	"""
	norm = _normalize(phone)
	if not norm:
		return None

	contact_name, contact_employee = _lookup_contact(norm)
	emp_name, emp_id = _lookup_employee(norm)

	# If neither Contact nor Employee gave us an employee id, try Trip Crew
	# Member — its mobile_number is usually fetched from the Employee but
	# occasionally that's the only place the number lives cleanly.
	crew_name, crew_emp_id = (None, None)
	if not contact_employee and not emp_id:
		crew_name, crew_emp_id = _lookup_trip_crew_member(norm)

	name = contact_name or emp_name or crew_name
	employee = contact_employee or emp_id or crew_emp_id

	if not name:
		return None

	designation = ""
	if employee:
		designation = frappe.db.get_value("Employee", employee, "designation") or ""

	return {
		"name": name,
		"employee": employee,
		"designation": designation,
		"vehicle_label": _most_recent_vehicle(employee) if employee else "",
	}


def _normalize(phone):
	"""Return the last 10 digits — matches Indian mobiles regardless of
	leading 0 or +91 prefix in storage."""
	if not phone:
		return ""
	digits = "".join(c for c in str(phone) if c.isdigit())
	return digits[-10:] if len(digits) >= 10 else digits


def _lookup_contact(norm):
	rows = frappe.db.get_all(
		"Contact Phone",
		filters={"phone": ("like", f"%{norm}%")},
		fields=["parent"],
		limit=1,
	)
	if not rows:
		return None, None

	try:
		contact = frappe.get_doc("Contact", rows[0].parent)
	except Exception:
		return None, None

	name = (contact.full_name or contact.first_name or "").strip()
	employee = None
	for link in (contact.links or []):
		if link.link_doctype == "Employee":
			employee = link.link_name
			break

	return (name or None, employee)


def _lookup_employee(norm):
	"""Match Employee on custom_mobile_number first (preferred in this
	bench — trip-tracking code reads from there), then cell_number."""
	for field in ("custom_mobile_number", "cell_number"):
		try:
			rows = frappe.db.get_all(
				"Employee",
				filters={field: ("like", f"%{norm}%")},
				fields=["name", "employee_name"],
				limit=1,
			)
		except Exception:
			# Field may not exist on this bench — try the next one.
			continue
		if rows:
			emp = rows[0]
			return (emp.employee_name or emp.name, emp.name)
	return None, None


def _lookup_trip_crew_member(norm):
	"""Return (employee_name, employee_id) from a Trip Crew Member whose
	own `mobile_number` matches the normalized digits."""
	try:
		rows = frappe.db.get_all(
			"Trip Crew Member",
			filters={"mobile_number": ("like", f"%{norm}%")},
			fields=["employee", "employee_name"],
			limit=1,
		)
	except Exception:
		return None, None
	if not rows:
		return None, None
	row = rows[0]
	return (row.employee_name or row.employee, row.employee)


def _most_recent_vehicle(employee):
	"""Scan every Trip Roster Assignment this employee appears on and
	return the license plate of the vehicle from the most recent one."""
	if not employee:
		return ""

	crew = frappe.db.get_all(
		"Trip Crew Member",
		filters={"employee": employee},
		pluck="name",
	)
	if not crew:
		return ""

	rows = frappe.db.sql(
		"""
		SELECT vehicle
		FROM `tabTrip Roster Assignment`
		WHERE (driver_1 IN %(crew)s OR driver_2 IN %(crew)s)
		  AND vehicle IS NOT NULL AND vehicle != ''
		ORDER BY date DESC
		LIMIT 1
		""",
		{"crew": tuple(crew)},
		as_dict=True,
	)
	if not rows:
		return ""

	vehicle = rows[0].vehicle
	return frappe.db.get_value("Vehicle", vehicle, "license_plate") or vehicle
