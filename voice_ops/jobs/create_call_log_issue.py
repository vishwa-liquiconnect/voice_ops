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
	}
	if priority:
		doc["priority"] = priority
	raised_by = call_log.get("from")
	if raised_by:
		doc["raised_by"] = raised_by

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


def _enrich_caller(call_log):
	try:
		from voice_ops.services.caller_lookup import resolve_caller

		info = resolve_caller(call_log.get("from")) or {}
	except Exception:
		info = {}
	call_log["caller_name"] = info.get("name") or ""
	call_log["caller_designation"] = info.get("designation") or ""
	call_log["caller_vehicle"] = info.get("vehicle_label") or ""
