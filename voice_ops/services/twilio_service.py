"""
Twilio Service

Thin wrapper around twilio_integration's initiate_twilio_call.
Uses Twilio Settings for credentials and Twilio Call Log for tracking.
"""

import frappe
from frappe.utils import get_url


def initiate_call(to_number, reference_doctype=None, reference_name=None):
	"""
	Initiate an automated outbound call via Twilio.

	Uses twilio_integration app's initiate_twilio_call which handles
	credentials, Call Log creation, and retry logic.

	Args:
		to_number: Driver's mobile number
		reference_doctype: DocType to link in Twilio Call Log (e.g. "Checklist Run")
		reference_name: Document name to link

	Returns:
		Twilio Call Log document name
	"""
	from twilio_integration.twilio_integration.doctype.twilio_call_log.twilio_call_log import (
		force_https,
		initiate_twilio_call,
		normalize_mobile_no,
	)

	to_number = normalize_mobile_no(to_number)

	site_url = get_url()
	twiml_url = force_https(f"{site_url}/api/method/voice_ops.api.twilio_webhook.twiml_response")

	result = initiate_twilio_call(
		to_number=to_number,
		twiml_url=twiml_url,
		purpose="Voice Ops Checklist",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	return result.get("log")
