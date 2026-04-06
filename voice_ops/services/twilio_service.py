"""
Twilio Service

Thin wrapper that delegates to the telephony abstraction layer.
Kept for backward compatibility with existing callers.
"""

import frappe
from frappe.utils import get_url

from voice_ops.services.telephony import force_https, initiate_call as _initiate_call


def initiate_call(to_number, reference_doctype=None, reference_name=None):
	"""
	Initiate an automated outbound call via the configured telephony provider.

	Args:
		to_number: Driver's mobile number
		reference_doctype: DocType to link in call log (e.g. "Checklist Run")
		reference_name: Document name to link

	Returns:
		Call log document name
	"""
	site_url = get_url()
	provider = frappe.db.get_single_value("Voice Ops Settings", "telephony_provider") or "Twilio"

	if provider == "Exotel":
		endpoint = "voice_ops.api.exotel_webhook.exoml_response"
	else:
		endpoint = "voice_ops.api.twilio_webhook.twiml_response"

	base_url = force_https(f"{site_url}/api/method/{endpoint}")
	callback_url = f"{base_url}?checklist_run={reference_name}" if reference_name else base_url

	return _initiate_call(
		to_number=to_number,
		twiml_url=callback_url,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
