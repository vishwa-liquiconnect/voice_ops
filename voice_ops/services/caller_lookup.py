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

	Returns None when no Contact or Employee match is found.
	When an Employee is found but has never been rostered, `vehicle_label`
	is an empty string.
	"""
	norm = _normalize(phone)
	if not norm:
		return None

	name, employee = _lookup_contact(norm)
	if not name:
		name, employee = _lookup_employee(norm)

	if not name:
		return None

	return {
		"name": name,
		"employee": employee,
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
