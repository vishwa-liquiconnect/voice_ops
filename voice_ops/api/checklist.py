"""
Checklist API

Endpoints for creating and triggering checklist calls manually.
Auto-triggering is handled by jobs/auto_trigger.py.
"""

import frappe
from frappe.utils import now_datetime

from voice_ops.services.twilio_service import initiate_call


@frappe.whitelist()
def trigger_checklist_call(checklist_run_name):
	"""
	Trigger an outbound Twilio call for an existing Checklist Run.

	The Checklist Run must be in Draft status with a valid mobile number.
	"""
	run = frappe.get_doc("Checklist Run", checklist_run_name)

	if run.status not in ("Draft", "Call Initiated"):
		frappe.throw(f"Cannot trigger call: Checklist Run is in '{run.status}' status")

	if not run.mobile_number:
		frappe.throw("No mobile number available for this crew member")

	# Populate responses from template if not already done
	if not run.responses:
		run.populate_responses_from_template()
		run.save(ignore_permissions=True)

	# Initiate the call via Twilio
	call_log_name = initiate_call(
		to_number=run.mobile_number,
		reference_doctype="Checklist Run",
		reference_name=run.name,
	)

	# Link the Call Log to the Checklist Run
	run.call_log = call_log_name
	run.status = "Call Initiated"
	run.initiated_at = now_datetime()
	run.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"checklist_run": run.name,
		"call_log": call_log_name,
		"status": "Call Initiated",
	}


@frappe.whitelist()
def create_and_trigger_checklist(template_name, crew_member, trip_roster_assignment=None, vehicle=None):
	"""
	Create a Checklist Run from a template and trigger the call.

	This is the manual entry point for initiating a voice checklist.
	"""
	template = frappe.get_doc("Checklist Template", template_name)
	if not template.is_active:
		frappe.throw(f"Checklist Template '{template_name}' is not active")

	run = frappe.get_doc({
		"doctype": "Checklist Run",
		"checklist_template": template_name,
		"crew_member": crew_member,
		"trip_roster_assignment": trip_roster_assignment,
		"vehicle": vehicle,
	})
	run.insert(ignore_permissions=True)

	run.populate_responses_from_template()
	run.save(ignore_permissions=True)
	frappe.db.commit()

	return trigger_checklist_call(run.name)
