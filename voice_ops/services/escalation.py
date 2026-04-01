"""
Escalation Service

Handles automated actions triggered by checklist evaluation results,
per the SOP Action & Escalation Matrix:

  Driver not fit     -> Block trip & escalate
  Crew missing       -> Alert operations
  Document missing   -> Collect & escalate
  Safety missing     -> Critical escalation
  AC issue           -> Maintenance ticket
  Mechanical issue   -> Block vehicle
  Linen overdue      -> Notify vendor
  Diesel excess      -> Flag analytics
  Incident           -> Immediate escalation

All escalation actions are deterministic. AI does NOT decide escalations.
"""

import frappe
from frappe.utils import now_datetime


# Maps escalation type -> severity and action description
ESCALATION_CONFIG = {
	"block_trip": {
		"severity": "Critical",
		"subject": "Trip Blocked: {label}",
		"action": "Block trip and escalate to operations head",
	},
	"crew_missing": {
		"severity": "High",
		"subject": "Crew Alert: {label}",
		"action": "Alert operations team for crew replacement",
	},
	"document_missing": {
		"severity": "High",
		"subject": "Document Missing: {label}",
		"action": "Collect documents and escalate to compliance",
	},
	"safety_missing": {
		"severity": "Critical",
		"subject": "Safety Critical: {label}",
		"action": "Critical safety escalation - immediate attention required",
	},
	"maintenance_ticket": {
		"severity": "Medium",
		"subject": "Maintenance Required: {label}",
		"action": "Create maintenance ticket for vehicle",
	},
	"block_vehicle": {
		"severity": "Critical",
		"subject": "Vehicle Blocked: {label}",
		"action": "Block vehicle from service until issue resolved",
	},
	"linen_overdue": {
		"severity": "Low",
		"subject": "Linen Overdue: {label}",
		"action": "Notify linen vendor for pickup/delivery",
	},
	"diesel_excess": {
		"severity": "Medium",
		"subject": "Diesel Exception: {label}",
		"action": "Flag for analytics review",
	},
	"incident": {
		"severity": "Critical",
		"subject": "Incident Report: {label}",
		"action": "Immediate escalation to management",
	},
}


def process_escalations(checklist_run_name, escalation_triggers):
	"""
	Process all escalation triggers from a checklist evaluation.

	Args:
		checklist_run_name: Name of the Checklist Run document
		escalation_triggers: List of dicts with type, question_key, label
	"""
	if not escalation_triggers:
		return

	settings = frappe.get_single("Voice Ops Settings")
	if not settings.enable_escalation:
		return

	run = frappe.get_doc("Checklist Run", checklist_run_name)

	for trigger in escalation_triggers:
		try:
			_process_single_escalation(run, trigger, settings)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Escalation failed for {checklist_run_name} - {trigger.get('type')}",
			)


def _process_single_escalation(run, trigger, settings):
	"""Process a single escalation trigger."""
	escalation_type = trigger.get("type")
	config = ESCALATION_CONFIG.get(escalation_type)
	if not config:
		return

	label = trigger.get("label", trigger.get("question_key", ""))
	subject = config["subject"].format(label=label)

	# Build context for the notification
	context = _build_escalation_context(run, trigger, config)

	# Create a system notification (Notification Log) for in-app alerting
	_create_notification(run, subject, context, config["severity"], settings)

	# Send email if escalation email is configured
	if settings.escalation_email_group:
		_send_escalation_email(run, subject, context, config, settings)

	# Log the escalation as a comment on the Checklist Run
	frappe.get_doc("Checklist Run", run.name).add_comment(
		"Info",
		f"Escalation [{config['severity']}]: {subject}\nAction: {config['action']}",
	)


def _build_escalation_context(run, trigger, config):
	"""Build context dict for escalation messages."""
	return {
		"checklist_run": run.name,
		"crew_member": run.crew_member_name or run.crew_member,
		"vehicle": run.vehicle or "N/A",
		"trip": run.trip_roster_assignment or "N/A",
		"template": run.checklist_template,
		"severity": config["severity"],
		"action_required": config["action"],
		"question_key": trigger.get("question_key"),
		"issue": trigger.get("label"),
		"timestamp": now_datetime(),
	}


def _create_notification(run, subject, context, severity, settings):
	"""Create an in-app notification for the escalation."""
	# Notify Fleet Manager role users
	fleet_managers = frappe.get_all(
		"Has Role",
		filters={"role": "Fleet Manager", "parenttype": "User"},
		fields=["parent"],
	)

	for fm in fleet_managers:
		user = fm["parent"]
		if not frappe.db.exists("User", {"name": user, "enabled": 1}):
			continue

		notification = frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": user,
			"from_user": "Administrator",
			"type": "Alert",
			"document_type": "Checklist Run",
			"document_name": run.name,
			"subject": f"[{severity}] {subject}",
			"email_content": (
				f"<b>Checklist Run:</b> {run.name}<br>"
				f"<b>Crew:</b> {context['crew_member']}<br>"
				f"<b>Vehicle:</b> {context['vehicle']}<br>"
				f"<b>Trip:</b> {context['trip']}<br>"
				f"<b>Issue:</b> {context['issue']}<br>"
				f"<b>Action Required:</b> {context['action_required']}"
			),
		})
		notification.insert(ignore_permissions=True)


def _send_escalation_email(run, subject, context, config, settings):
	"""Send escalation email to configured email group."""
	recipients = _get_email_recipients(settings.escalation_email_group)
	if not recipients:
		return

	message = f"""
	<h3>Voice Ops Escalation - {config['severity']}</h3>
	<table style="border-collapse: collapse; width: 100%;">
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Checklist Run</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{run.name}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Crew Member</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{context['crew_member']}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Vehicle</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{context['vehicle']}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Trip</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{context['trip']}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Issue</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{context['issue']}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Action Required</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{context['action_required']}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Timestamp</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{context['timestamp']}</td></tr>
	</table>
	"""

	frappe.sendmail(
		recipients=recipients,
		subject=f"[Voice Ops - {config['severity']}] {subject}",
		message=message,
		reference_doctype="Checklist Run",
		reference_name=run.name,
	)


def _get_email_recipients(email_group_name):
	"""Get email addresses from an Email Group."""
	if not email_group_name:
		return []

	if not frappe.db.exists("Email Group", email_group_name):
		frappe.log_error(
			f"Email Group '{email_group_name}' not found",
			"Voice Ops: Escalation Email Group Missing",
		)
		return []

	members = frappe.get_all(
		"Email Group Member",
		filters={"email_group": email_group_name, "unsubscribed": 0},
		fields=["email"],
	)
	return [m["email"] for m in members]
