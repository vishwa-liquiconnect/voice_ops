"""
Post-Issue Acknowledgements

After an Issue is created from a Call Log, fan out two WhatsApp
notifications:

- Route manager (English) — ticket ID, caller, vehicle, transcript
  excerpt. Resolved via `operations_incharge` on the driver's most
  recent Trip Roster Assignment.
- Driver (native language) — short confirmation that the concern was
  logged as Issue X. Language resolved via the chain in
  `voice_ops.services.language.resolve_driver_language`.

Both sends are best-effort and log on failure; they never fail the
Issue creation itself.
"""

import frappe


_DRIVER_ACK_EN = (
	"We have received your voicemail and logged it as ticket {issue_id}."
	" Our team will follow up shortly. — Voice Ops"
)

_ROUTE_MANAGER_TEMPLATE = (
	"*Voice Ops — New Issue*\n"
	"Ticket: {issue_id}\n"
	"Priority: {priority}\n"
	"Caller: {caller}\n"
	"Phone: {phone}\n"
	"Vehicle: {vehicle}\n\n"
	"Excerpt: {excerpt}\n\n"
	"Open: {url}"
)


def send_route_manager_alert(issue_name, call_log, route_manager):
	"""WhatsApp the resolved route manager in English."""
	settings = frappe.get_single("Voice Ops Settings")
	if not getattr(settings, "enable_route_manager_whatsapp", 0):
		return
	if not route_manager or not route_manager.get("phone"):
		return

	from voice_ops.services.telephony import send_whatsapp
	from frappe.utils import get_url

	phone = _normalize_msisdn(route_manager["phone"])
	if not phone:
		return

	priority = frappe.db.get_value("Issue", issue_name, "priority") or "Medium"
	caller = call_log.get("caller_name") or "Unknown"
	caller_phone = call_log.get("from") or "-"
	vehicle = call_log.get("caller_vehicle") or "-"
	excerpt = _truncate(call_log.get("summary") or "", 240)
	url = get_url(f"/app/issue/{issue_name}")

	body = _ROUTE_MANAGER_TEMPLATE.format(
		issue_id=issue_name,
		priority=priority,
		caller=caller,
		phone=caller_phone,
		vehicle=vehicle,
		excerpt=excerpt or "(no transcript)",
		url=url,
	)

	try:
		send_whatsapp(phone, body)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Route manager WhatsApp failed for {issue_name}",
		)


def send_driver_ack(issue_name, call_log):
	"""WhatsApp the driver in their native language."""
	settings = frappe.get_single("Voice Ops Settings")
	if not getattr(settings, "enable_driver_ack_whatsapp", 0):
		return

	phone = _normalize_msisdn(call_log.get("from") or "")
	if not phone:
		return

	from voice_ops.services.language import resolve_driver_language, translate_to

	language_code = resolve_driver_language(call_log)
	english = _DRIVER_ACK_EN.format(issue_id=issue_name)
	body = translate_to(english, language_code) if language_code and not language_code.startswith("en") else english

	from voice_ops.services.telephony import send_whatsapp
	try:
		send_whatsapp(phone, body)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Driver ack WhatsApp failed for {issue_name}",
		)


def _normalize_msisdn(phone):
	"""Return a `+<country><number>` form suitable for Twilio WhatsApp.
	Defaults to +91 for 10-digit Indian numbers. Returns '' when the
	input can't be normalized."""
	if not phone:
		return ""
	raw = str(phone).strip()
	if raw.startswith("+"):
		return raw
	digits = "".join(ch for ch in raw if ch.isdigit())
	if not digits:
		return ""
	if len(digits) == 10:
		return f"+91{digits}"
	if len(digits) == 11 and digits.startswith("0"):
		return f"+91{digits[1:]}"
	if len(digits) == 12 and digits.startswith("91"):
		return f"+{digits}"
	return f"+{digits}"


def _truncate(text, limit):
	text = (text or "").strip()
	if len(text) <= limit:
		return text
	return text[: limit - 1].rstrip() + "…"
