"""
Call Log → ERPNext Issue

Enqueued after a Call Log's summary is populated. Uses Claude (via
`services.summarizer.generate_issue_payload`) to derive the Issue
subject, description, and priority, sourcing business context from
FMS AI Settings. Creates the Issue deterministically; Claude only
shapes the content.
"""

import frappe


ALLOWED_PRIORITIES = ("Low", "Medium", "High")
PRIORITY_FALLBACK = "Medium"
ALLOWED_LEVELS = ("Vehicle", "Office")
LEVEL_FALLBACK = "Vehicle"


def run(call_log_name):
	"""Create an ERPNext Issue from the given Call Log, if eligible."""
	if not call_log_name or not frappe.db.exists("Call Log", call_log_name):
		return

	call_log = frappe.db.get_value(
		"Call Log",
		call_log_name,
		["name", "summary", "from", "to", "type_of_call", "duration"],
		as_dict=True,
	)
	summary = (call_log.summary or "").strip() if call_log else ""
	if not summary:
		return

	if _issue_already_created(call_log_name):
		return

	_enrich_caller(call_log)

	from voice_ops.services.summarizer import generate_issue_payload

	payload = generate_issue_payload(call_log, summary)
	if not payload:
		return

	subject = (payload.get("subject") or "").strip() or f"Call {call_log_name}"
	description = (payload.get("description") or "").strip() or summary
	priority = _resolve_priority(payload.get("priority"))
	level = _resolve_level(payload.get("level"))

	description_with_ref = (
		f"{description}"
		f"<hr><p><em>Auto-created from Call Log "
		f'<a href="/app/call-log/{call_log_name}">{call_log_name}</a></em></p>'
	)

	doc = {
		"doctype": "Issue",
		"subject": subject[:140],
		"description": description_with_ref,
		"status": "Open",
		"custom_select_level": level,
	}
	if priority:
		doc["priority"] = priority

	contact_id = call_log.get("caller_contact")
	if contact_id and frappe.db.exists("Contact", contact_id):
		doc["contact"] = contact_id

	email = (call_log.get("caller_email") or "").strip()
	if email and "@" in email:
		doc["raised_by"] = email

	vehicle_id = call_log.get("caller_vehicle_id")
	if level == "Vehicle" and vehicle_id and frappe.db.exists("Vehicle", vehicle_id):
		doc["custom_vehicle"] = vehicle_id

	company = _resolve_company()
	if company:
		doc["company"] = company

	try:
		issue = frappe.get_doc(doc)
		issue.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Issue creation failed for {call_log_name}",
		)


def _issue_already_created(call_log_name):
	return bool(
		frappe.db.exists(
			"Issue",
			{"description": ["like", f"%/app/call-log/{call_log_name}%"]},
		)
	)


def _resolve_priority(raw):
	if not raw:
		return _fallback_priority()
	value = str(raw).strip().title()
	if value in ALLOWED_PRIORITIES and frappe.db.exists("Issue Priority", value):
		return value
	return _fallback_priority()


def _fallback_priority():
	return PRIORITY_FALLBACK if frappe.db.exists("Issue Priority", PRIORITY_FALLBACK) else None


def _resolve_level(raw):
	if raw:
		value = str(raw).strip().title()
		if value in ALLOWED_LEVELS:
			return value
	return LEVEL_FALLBACK


def _enrich_caller(call_log):
	try:
		from voice_ops.services.caller_lookup import resolve_caller

		info = resolve_caller(call_log.get("from")) or {}
	except Exception:
		info = {}
	call_log["caller_name"] = info.get("name") or ""
	call_log["caller_designation"] = info.get("designation") or ""
	call_log["caller_vehicle"] = info.get("vehicle_label") or ""
	call_log["caller_contact"] = info.get("contact") or ""
	call_log["caller_vehicle_id"] = info.get("vehicle") or ""
	call_log["caller_email"] = info.get("email") or ""


def _resolve_company():
	"""Pick a Company doc name to link on the Issue.

	Preference order: FMS AI Settings' `company_name` if it resolves to
	an existing Company doc, otherwise the ERPNext default company.
	Returns None when nothing matches.
	"""
	try:
		candidate = frappe.db.get_single_value("FMS AI Settings", "company_name") or ""
	except Exception:
		candidate = ""
	candidate = candidate.strip()
	if candidate and frappe.db.exists("Company", candidate):
		return candidate
	try:
		return frappe.defaults.get_global_default("company")
	except Exception:
		return None
