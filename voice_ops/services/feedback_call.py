"""
Outbound Feedback Call Service

Places an outbound Exotel call to a driver. Two execution paths,
selected by configuration in Voice Ops Settings:

1. **App flow** (legacy) — `exotel_feedback_flow_app_id` is set. Exotel
   runs a static App Builder flow (Greeting -> Record -> Hangup). Found
   in practice to misbehave on outbound: audio plays on the wrong leg,
   driver hears silence.

2. **ExoML flow** (preferred) — `exotel_feedback_flow_app_id` is blank.
   Exotel fetches `voice_ops.api.feedback.feedback_exoml` at call time
   and plays the returned <Play>/<Record> XML. Matches the working
   inbound pattern and avoids the connect.json click-to-call leg
   ambiguity.

Either way, the recording ends up on a Call Log tagged
`type_of_call = "Feedback"` and flows through the same ingestion
pipeline as voicemails (download -> Sarvam transcribe -> store in
Call Log.summary).
"""

import frappe

from voice_ops.services.telephony import _initiate_exotel_call, build_callback_url


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
	Trigger an outbound feedback call via Exotel.

	Args:
		to_number: Direct phone number. Takes precedence if given.
		employee: Employee ID. Used to resolve the phone number when
			to_number is not provided.
		reference_doctype, reference_name: Optional doctype + name to link
			on the Call Log (e.g. Trip Roster Assignment, Checklist Run).

	Returns:
		Call Log name (str).
	"""
	settings = frappe.get_single("Voice Ops Settings")
	flow_app_id = (settings.exotel_feedback_flow_app_id or "").strip()

	if not to_number and employee:
		to_number = _resolve_driver_phone(employee)

	if not to_number:
		frappe.throw("No phone number available for the feedback call.")

	custom_field = None
	if reference_doctype and reference_name:
		custom_field = f"{reference_doctype}={reference_name}"
	elif employee:
		custom_field = f"employee={employee}"

	# ExoML path when no App ID is configured. Exotel will fetch this
	# URL when the call connects and play the returned XML on the
	# callee's leg.
	callback_url = None
	if not flow_app_id:
		callback_url = build_callback_url("voice_ops.api.feedback.feedback_exoml")

	return _initiate_exotel_call(
		to_number=to_number,
		callback_url=callback_url,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		flow_app_id=flow_app_id or None,
		type_of_call="Feedback",
		custom_field=custom_field,
	)
