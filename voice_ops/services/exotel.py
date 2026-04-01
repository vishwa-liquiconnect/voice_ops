"""
Exotel Service

Initiates automated outbound calls using the exotel_integration app's
credentials and Call Log doctype. This is different from the desk
click-to-call flow — no logged-in employee is needed.
"""

import frappe
import requests
from frappe import _

from exotel_integration.handler import (
	create_call_log,
	get_exotel_endpoint,
	get_status_updater_url,
	is_integration_enabled,
)


def initiate_call(to_number, caller_id=None, reference_doctype=None, reference_name=None):
	"""
	Initiate an automated outbound call via Exotel.

	Unlike exotel_integration's make_a_call (which is desk-user click-to-call),
	this function is designed for background job calls — no employee lookup needed.

	Args:
		to_number: Driver's mobile number
		caller_id: ExoPhone number (optional, fetched from exophones if not provided)
		reference_doctype: DocType to link in Call Log (e.g. "Checklist Run")
		reference_name: Document name to link

	Returns:
		Call Log document name
	"""
	if not is_integration_enabled():
		frappe.throw(_("Exotel integration is not enabled"), title=_("Integration Not Enabled"))

	if not caller_id:
		caller_id = _get_default_caller_id()

	endpoint = get_exotel_endpoint("Calls/connect.json?details=true")
	record_call = frappe.db.get_single_value("Exotel Settings", "record_call")

	try:
		response = requests.post(
			endpoint,
			data={
				"From": to_number,
				"CallerId": caller_id,
				"Record": "true" if record_call else "false",
				"StatusCallback": get_status_updater_url(),
				"StatusCallbackEvents[0]": "terminal",
				"StatusCallbackEvents[1]": "answered",
			},
			timeout=30,
		)
		response.raise_for_status()
	except requests.exceptions.HTTPError:
		error_msg = ""
		try:
			exc = response.json().get("RestException", {})
			error_msg = exc.get("Message", str(response.text))
		except Exception:
			error_msg = str(response.text)

		frappe.log_error(
			f"Exotel call failed to {to_number}: {error_msg}",
			"Voice Ops: Exotel Call Failed",
		)
		frappe.throw(_(f"Failed to initiate call: {error_msg}"), title=_("Exotel Error"))
	except requests.exceptions.RequestException as e:
		frappe.log_error(
			f"Exotel request error for {to_number}: {e}",
			"Voice Ops: Exotel Request Error",
		)
		frappe.throw(_(f"Failed to connect to Exotel: {e}"), title=_("Connection Error"))

	res = response.json()
	call_payload = res.get("Call", {})

	# Build link_to_document for Call Log
	link_to_document = None
	if reference_doctype and reference_name:
		link_to_document = {
			"link_doctype": reference_doctype,
			"link_name": reference_name,
		}

	call_log = create_call_log(
		call_id=call_payload.get("Sid"),
		from_number=call_payload.get("From"),
		to_number=call_payload.get("To"),
		medium=call_payload.get("PhoneNumberSid"),
		call_type="Outgoing",
		link_to_document=link_to_document,
	)

	return call_log.name


def _get_default_caller_id():
	"""Get the first available ExoPhone as caller ID."""
	try:
		from exotel_integration.handler import get_all_exophones
		exophones = get_all_exophones()
		if exophones:
			return exophones[0]
	except Exception:
		pass

	frappe.throw(
		_("No ExoPhone number available. Please configure caller ID."),
		title=_("Missing Caller ID"),
	)
