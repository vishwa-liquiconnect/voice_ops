"""
Query Notification Service

Sends driver query summaries to the MD and route manager via:
1. WhatsApp (primary, via Twilio Messages API)
2. Email (fallback, via frappe.sendmail)
3. In-app notification (Notification Log)
"""

import frappe
from frappe.utils import get_url

from voice_ops.services.telephony import send_whatsapp


def notify_query(driver_query_name):
	"""
	Send notifications for a processed driver query.

	Notifies the MD and the route manager (operations_incharge)
	via WhatsApp, email, and in-app notifications.

	Args:
		driver_query_name: Name of the Driver Query document
	"""
	dq = frappe.get_doc("Driver Query", driver_query_name)
	settings = frappe.get_single("Voice Ops Settings")

	caller_display = dq.verbal_name or dq.crew_member_name or dq.caller_phone or "Unknown"
	bus_display = dq.verbal_bus or (dq.vehicle or "Unknown")
	summary = dq.query_summary or dq.query_transcript or "No query recorded."
	dq_url = get_url(f"/app/driver-query/{dq.name}")

	errors = []

	# --- Notify MD ---
	md_phone = settings.md_phone
	md_email = settings.md_email

	if md_phone and settings.enable_whatsapp_notifications:
		try:
			_send_whatsapp_notification(md_phone, caller_display, bus_display, summary, dq, dq_url)
			dq.md_notified = 1
		except Exception as e:
			errors.append(f"MD WhatsApp: {e}")

	if md_email and settings.enable_email_notifications:
		try:
			_send_email_notification(
				[md_email], caller_display, bus_display, summary, dq, dq_url
			)
			dq.md_notified = 1
		except Exception as e:
			errors.append(f"MD Email: {e}")

	# In-app notification for MD (if they have a User account)
	_create_in_app_notification(dq, caller_display, summary, role="System Manager")

	# --- Notify Route Manager ---
	if dq.route_manager:
		rm_phone, rm_email, rm_user = _get_employee_contact(dq.route_manager)

		if rm_phone and settings.enable_whatsapp_notifications:
			try:
				_send_whatsapp_notification(rm_phone, caller_display, bus_display, summary, dq, dq_url)
				dq.route_manager_notified = 1
			except Exception as e:
				errors.append(f"Route Manager WhatsApp: {e}")

		if rm_email and settings.enable_email_notifications:
			try:
				_send_email_notification(
					[rm_email], caller_display, bus_display, summary, dq, dq_url
				)
				dq.route_manager_notified = 1
			except Exception as e:
				errors.append(f"Route Manager Email: {e}")

		if rm_user:
			_create_in_app_notification_for_user(dq, caller_display, summary, rm_user)

	# Also notify Fleet Manager role users via in-app
	_create_in_app_notification(dq, caller_display, summary, role="Fleet Manager")

	# Save notification status
	if errors:
		dq.notification_error = "; ".join(errors)
	dq.save(ignore_permissions=True)
	frappe.db.commit()


def _send_whatsapp_notification(phone, caller_display, bus_display, summary, dq, dq_url):
	"""Send WhatsApp message with query summary."""
	message = (
		f"*Driver Query* ({dq.name})\n"
		f"From: {caller_display} ({dq.caller_phone})\n"
		f"Bus: {bus_display}\n"
		f"Time: {dq.called_at}\n\n"
		f"*Summary:*\n{summary}\n\n"
		f"View: {dq_url}"
	)

	result = send_whatsapp(phone, message)
	if not result:
		raise Exception(f"WhatsApp send returned no SID for {phone}")


def _send_email_notification(recipients, caller_display, bus_display, summary, dq, dq_url):
	"""Send email with query details and full transcript."""
	subject = f"[Voice Ops] Driver Query from {caller_display} - {bus_display}"

	message = f"""
	<h3>Driver Query - {dq.name}</h3>
	<table style="border-collapse: collapse; width: 100%;">
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Caller</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{caller_display} ({dq.caller_phone})</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Identified As</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{dq.crew_member_name or "Not identified"}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Bus</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{bus_display}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Vehicle (Matched)</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{dq.vehicle or "Not matched"}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Call Time</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{dq.called_at}</td></tr>
		<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Language</b></td>
			<td style="padding: 8px; border: 1px solid #ddd;">{dq.detected_language or "Unknown"}</td></tr>
	</table>

	<h4>Summary</h4>
	<p>{summary}</p>

	<h4>Full Transcript</h4>
	<p>{dq.query_transcript or "N/A"}</p>

	<p><a href="{dq_url}">View Driver Query</a></p>
	"""

	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=message,
		reference_doctype="Driver Query",
		reference_name=dq.name,
	)


def _create_in_app_notification(dq, caller_display, summary, role):
	"""Create in-app Notification Log for all users with given role."""
	users = frappe.get_all(
		"Has Role",
		filters={"role": role, "parenttype": "User"},
		fields=["parent"],
	)

	for row in users:
		user = row["parent"]
		if not frappe.db.exists("User", {"name": user, "enabled": 1}):
			continue
		_create_in_app_notification_for_user(dq, caller_display, summary, user)


def _create_in_app_notification_for_user(dq, caller_display, summary, user):
	"""Create a single in-app notification for a specific user."""
	# Avoid duplicates
	existing = frappe.db.exists("Notification Log", {
		"document_type": "Driver Query",
		"document_name": dq.name,
		"for_user": user,
	})
	if existing:
		return

	frappe.get_doc({
		"doctype": "Notification Log",
		"for_user": user,
		"from_user": "Administrator",
		"type": "Alert",
		"document_type": "Driver Query",
		"document_name": dq.name,
		"subject": f"Driver Query from {caller_display}",
		"email_content": (
			f"<b>Caller:</b> {caller_display} ({dq.caller_phone})<br>"
			f"<b>Bus:</b> {dq.verbal_bus or dq.vehicle or 'Unknown'}<br>"
			f"<b>Summary:</b> {summary[:200]}"
		),
	}).insert(ignore_permissions=True)


def _get_employee_contact(employee_name):
	"""
	Get phone, email, and user_id for an Employee.

	Returns (phone, email, user_id) tuple.
	"""
	emp = frappe.db.get_value(
		"Employee",
		employee_name,
		["cell_phone", "company_email", "personal_email", "user_id"],
		as_dict=True,
	)
	if not emp:
		return None, None, None

	phone = emp.get("cell_phone")
	email = emp.get("company_email") or emp.get("personal_email")
	user_id = emp.get("user_id")

	return phone, email, user_id
