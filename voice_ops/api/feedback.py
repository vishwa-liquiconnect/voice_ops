"""
Feedback Call API

Whitelisted entry point for triggering outbound feedback calls to drivers.
Thin wrapper over `voice_ops.services.feedback_call.initiate_feedback_call`.
"""

import frappe

from voice_ops.services.feedback_call import initiate_feedback_call


@frappe.whitelist()
def trigger_feedback_call(to_number=None, employee=None,
                          reference_doctype=None, reference_name=None):
	"""
	Trigger an outbound Exotel feedback call.

	Provide either `to_number` directly or an `employee` (Employee ID) to
	resolve a phone number from. Optionally link a reference doctype +
	name on the Call Log for context.
	"""
	call_log_name = initiate_feedback_call(
		to_number=to_number,
		employee=employee,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
	return {"call_log": call_log_name, "status": "Call Initiated"}
