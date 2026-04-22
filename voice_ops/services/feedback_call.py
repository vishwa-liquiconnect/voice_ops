"""
Outbound Feedback Call Service

Places an outbound Exotel call to a driver using a static App Builder flow
(Greeting -> Record -> Hangup). The recording gets tagged `type_of_call
= "Feedback"` and flows through the same ingestion pipeline as voicemails
(download -> Sarvam transcribe -> store in Call Log.summary).

The App flow itself is built in the Exotel dashboard; this service only
triggers the call and tags the resulting Call Log.
"""

import frappe

from voice_ops.services.telephony import _initiate_exotel_call


def _resolve_driver_phone(employee):
	"""Return the best phone number on file for a driver Employee, or None."""
	if not employee:
		return None
	row = frappe.db.get_value(
		"Employee", employee,
		["cell_number", "personal_phone", "employee_name"],
		as_dict=True,
	)
	if not row:
		return None
	return row.get("cell_number") or row.get("personal_phone")


def initiate_feedback_call(to_number=None, employee=None,
                            reference_doctype=None, reference_name=None):
	"""
	Trigger an outbound feedback call via Exotel's static App flow.

	Args:
		to_number: Direct phone number. Takes precedence if given.
		employee: Employee ID. Used to resolve the phone number when
			to_number is not provided.
		reference_doctype, reference_name: Optional doctype + name to link
			on the Call Log (e.g. Trip Roster Assignment, Checklist Run).

	Returns:
		Call Log name (str).

	Raises:
		frappe.ValidationError if the feedback App ID is not configured,
		or if neither to_number nor a resolvable employee phone is given.
	"""
	flow_app_id = (
		frappe.db.get_single_value("Voice Ops Settings", "exotel_feedback_flow_app_id") or ""
	).strip()
	if not flow_app_id:
		frappe.throw(
			"Feedback Flow App ID not configured. "
			"Set Voice Ops Settings -> Exotel -> Feedback Flow App ID."
		)

	if not to_number and employee:
		to_number = _resolve_driver_phone(employee)

	if not to_number:
		frappe.throw("No phone number available for the feedback call.")

	custom_field = None
	if reference_doctype and reference_name:
		custom_field = f"{reference_doctype}={reference_name}"
	elif employee:
		custom_field = f"employee={employee}"

	return _initiate_exotel_call(
		to_number=to_number,
		callback_url=None,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		flow_app_id=flow_app_id,
		type_of_call="Feedback",
		custom_field=custom_field,
	)
