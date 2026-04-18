"""
Caller Identity Lookup

Resolves a mobile number to a caller name (via Contact or Employee) and
the most-recently-assigned vehicle (via Trip Crew Member → Trip Roster
Assignment). Used to enrich voicemail digests so operators see "Rajesh
on TN-09-AB-1234" instead of a raw phone number.
"""

import frappe


def resolve_caller(phone):
	"""Resolve a phone number to `{name, contact, employee, email, vehicle, vehicle_label}`.

	Runs three independent lookups (Contact, Employee, Trip Crew Member)
	and merges: prefers a Contact-side human name, but still picks up the
	Employee id from any source so the vehicle lookup can run. Returns
	None when no source matches at all.
	"""
	norm = _normalize(phone)
	if not norm:
		return None

	contact_id, contact_name, contact_employee, contact_email = _lookup_contact(norm)
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

	vehicle_id, vehicle_label = _most_recent_vehicle(employee) if employee else ("", "")
	email = contact_email or _employee_email(employee)

	return {
		"name": name,
		"contact": contact_id,
		"employee": employee,
		"designation": designation,
		"email": email,
		"vehicle": vehicle_id,
		"vehicle_label": vehicle_label,
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
		return None, None, None, None

	try:
		contact = frappe.get_doc("Contact", rows[0].parent)
	except Exception:
		return None, None, None, None

	name = (contact.full_name or contact.first_name or "").strip()
	employee = None
	for link in (contact.links or []):
		if link.link_doctype == "Employee":
			employee = link.link_name
			break

	email = _primary_contact_email(contact)

	return (contact.name, name or None, employee, email)


def _primary_contact_email(contact):
	"""Pick a usable email from a Contact: primary email_id first,
	else the first Contact Email child row."""
	email = (getattr(contact, "email_id", "") or "").strip()
	if email:
		return email
	for row in (contact.email_ids or []):
		candidate = (getattr(row, "email_id", "") or "").strip()
		if candidate:
			return candidate
	return ""


def _employee_email(employee):
	"""Prefer company_email, then user_id, then personal_email."""
	if not employee:
		return ""
	try:
		row = frappe.db.get_value(
			"Employee",
			employee,
			["company_email", "user_id", "personal_email"],
			as_dict=True,
		) or {}
	except Exception:
		return ""
	for key in ("company_email", "user_id", "personal_email"):
		value = (row.get(key) or "").strip()
		if value and "@" in value:
			return value
	return ""


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


def resolve_route_manager(employee):
	"""Return `{employee, name, phone}` for the operations_incharge on
	the employee's most recent Trip Roster Assignment, or None.

	The roster stores `operations_incharge` as an Employee link; we
	resolve that to a human-readable name and a WhatsApp-capable phone
	(custom_mobile_number first, then cell_number).
	"""
	if not employee:
		return None

	crew = frappe.db.get_all(
		"Trip Crew Member",
		filters={"employee": employee},
		pluck="name",
	)
	if not crew:
		return None

	rows = frappe.db.sql(
		"""
		SELECT operations_incharge
		FROM `tabTrip Roster Assignment`
		WHERE (driver_1 IN %(crew)s OR driver_2 IN %(crew)s)
		  AND operations_incharge IS NOT NULL AND operations_incharge != ''
		ORDER BY date DESC
		LIMIT 1
		""",
		{"crew": tuple(crew)},
		as_dict=True,
	)
	if not rows:
		return None

	rm_employee = rows[0].operations_incharge
	info = frappe.db.get_value(
		"Employee",
		rm_employee,
		["employee_name", "custom_mobile_number", "cell_number"],
		as_dict=True,
	) or {}

	phone = (info.get("custom_mobile_number") or info.get("cell_number") or "").strip()
	return {
		"employee": rm_employee,
		"name": info.get("employee_name") or rm_employee,
		"phone": phone,
	}


def _most_recent_vehicle(employee):
	"""Scan every Trip Roster Assignment this employee appears on and
	return `(vehicle_doc_name, license_plate)` from the most recent one.
	Falls back to using the doc name as the label when `license_plate`
	is empty."""
	if not employee:
		return "", ""

	crew = frappe.db.get_all(
		"Trip Crew Member",
		filters={"employee": employee},
		pluck="name",
	)
	if not crew:
		return "", ""

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
		return "", ""

	vehicle = rows[0].vehicle
	label = frappe.db.get_value("Vehicle", vehicle, "license_plate") or vehicle
	return vehicle, label
