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

	# Serialise Issue creation per Call Log. The upstream attach-recording
	# hook dedupes the enqueue, but if two jobs slip through (worker
	# restart, cache flush), this lock stops the second one before it
	# inserts a duplicate Issue. 5 min is plenty — Claude + insert finish
	# in seconds.
	lock_key = f"voice_ops:issue_creation:{call_log_name}"
	if frappe.cache().get_value(lock_key):
		return
	frappe.cache().set_value(lock_key, 1, expires_in_sec=300)

	call_log = frappe.db.get_value(
		"Call Log",
		call_log_name,
		["name", "summary", "from", "to", "type_of_call", "duration", "custom_detected_language"],
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

	# Outbound feedback calls: driver may have nothing to report, so only
	# create the Issue when Claude confirms the transcript is actionable.
	# Inbound voicemails always create an Issue — the use case is a driver
	# calling in *because* they have a problem, so non-actionable is a
	# near-zero case and we'd rather over-create than miss one.
	if call_log.get("type_of_call") == "Feedback" and not payload.get("is_actionable"):
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

	vehicle_id = _resolve_vehicle_from_hint(payload.get("vehicle_hint")) or call_log.get("caller_vehicle_id")
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
		return

	try:
		from voice_ops.services.call_log_links import add_call_log_links

		add_call_log_links(call_log_name, [("Issue", issue.name)])
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: failed to link Issue {issue.name} to Call Log {call_log_name}",
		)

	_dispatch_acks(issue.name, call_log)


def _dispatch_acks(issue_name, call_log):
	"""Send caller ack, then — only when the caller is a driver — loop in
	the route manager. Best-effort; individual failures are swallowed
	inside each sender.

	Definition of "driver": the caller's Employee has been assigned as
	`driver_1` or `driver_2` on at least one Trip Roster Assignment.
	`resolve_route_manager` enforces this by only returning a RM when
	such a TRA exists — so a None return value means "not a driver."
	Office staff, unknown contacts, or one-off Contacts therefore get
	the WhatsApp ack only; they do not trigger a route-manager alert.
	"""
	from voice_ops.services.ack_sender import send_driver_ack, send_route_manager_alert

	send_driver_ack(issue_name, call_log)

	employee = call_log.get("caller_employee")
	if not employee:
		return

	try:
		from voice_ops.services.caller_lookup import resolve_route_manager

		route_manager = resolve_route_manager(employee)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: route manager lookup failed for {issue_name}",
		)
		return

	if not route_manager:
		return

	send_route_manager_alert(issue_name, call_log, route_manager)


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


def _resolve_vehicle_from_hint(hint):
	"""Map Claude's transcript hint (e.g. '2833' or 'TN02BY2234') to a
	Vehicle doc name. Vehicles are named by license_plate, so we try:
	  1. exact match on the hint
	  2. exact match on the digits-only tail
	  3. license_plate ending with the digit tail (unique match only)
	  4. license_plate containing the digit tail (unique match only)
	Returns None when we can't pin down a single vehicle — better to
	fall back to the caller's assigned vehicle than link the wrong bus.
	"""
	if not hint:
		return None
	candidate = str(hint).strip().upper()
	if not candidate:
		return None

	if frappe.db.exists("Vehicle", candidate):
		return candidate

	digits = "".join(ch for ch in candidate if ch.isdigit())
	if not digits:
		return None

	if frappe.db.exists("Vehicle", digits):
		return digits

	for pattern in (f"%{digits}", f"%{digits}%"):
		rows = frappe.db.get_all(
			"Vehicle",
			filters={"license_plate": ("like", pattern)},
			pluck="name",
			limit=2,
		)
		if len(rows) == 1:
			return rows[0]
		if len(rows) > 1:
			return None
	return None


def _resolve_level(raw):
	if raw:
		value = str(raw).strip().title()
		if value in ALLOWED_LEVELS:
			return value
	return LEVEL_FALLBACK


def _enrich_caller(call_log):
	info = {}
	try:
		from voice_ops.services.caller_lookup import read_call_log_context, resolve_caller

		info = read_call_log_context(call_log.get("name")) or {}
		if not (info.get("employee") or info.get("contact") or info.get("vehicle")):
			info = resolve_caller(call_log.get("from")) or {}
	except Exception:
		info = {}
	call_log["caller_name"] = info.get("name") or ""
	call_log["caller_designation"] = info.get("designation") or ""
	call_log["caller_vehicle"] = info.get("vehicle_label") or ""
	call_log["caller_contact"] = info.get("contact") or ""
	call_log["caller_vehicle_id"] = info.get("vehicle") or ""
	call_log["caller_email"] = info.get("email") or ""
	call_log["caller_employee"] = info.get("employee") or ""


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
