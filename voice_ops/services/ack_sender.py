"""
Post-Issue Acknowledgements (Email + Exotel WhatsApp)

After an Issue is created from a Call Log, fan out two acks on each
enabled channel:

- Route manager — full context (ticket, priority, caller, vehicle,
  excerpt, CTA link). Sent to the `operations_incharge` on the
  caller's most recent Trip Roster Assignment.
- Driver — short confirmation. Sent to the caller.

Email and WhatsApp are independent, additive channels, each gated by
its own toggle in Voice Ops Settings. Email uses the resolved address
from Contact/Employee; WhatsApp uses Exotel's WhatsApp Business API
with pre-approved templates (template names configured in Voice Ops
Settings). Silent when the respective contact point cannot be resolved.

All sends are best-effort and log on failure; they never fail Issue
creation itself.
"""

import frappe
from frappe.utils import get_url

from voice_ops.services.telephony import send_exotel_whatsapp


def send_route_manager_alert(issue_name, call_log, route_manager):
	"""Notify the resolved route manager over email and Exotel WhatsApp.
	Each channel is gated independently and skipped silently when its
	contact point cannot be resolved."""
	settings = frappe.get_single("Voice Ops Settings")
	if not getattr(settings, "enable_route_manager_alert", 0):
		return
	if not route_manager:
		return

	priority = frappe.db.get_value("Issue", issue_name, "priority") or "Medium"
	caller = call_log.get("caller_name") or "Unknown"
	caller_phone = call_log.get("from") or "-"
	vehicle = (
		frappe.db.get_value("Issue", issue_name, "custom_vehicle")
		or call_log.get("caller_vehicle")
		or "-"
	)
	subject_line = frappe.db.get_value("Issue", issue_name, "subject") or "Voicemail Issue"
	excerpt = _truncate(call_log.get("summary") or "", 360)
	url = get_url(f"/app/issue/{issue_name}")
	company = _company_name()

	email = (route_manager.get("email") or "").strip()
	if email:
		subject = f"[{company}] New Issue {issue_name} · {priority}"
		html = _render_route_manager_html(
			company=company,
			issue_name=issue_name,
			issue_subject=subject_line,
			priority=priority,
			caller=caller,
			phone=caller_phone,
			vehicle=vehicle,
			excerpt=excerpt,
			url=url,
			manager_name=route_manager.get("name") or "",
		)
		try:
			frappe.sendmail(recipients=[email], subject=subject, message=html)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Route manager email failed for {issue_name}",
			)

	_send_route_manager_whatsapp(
		issue_name=issue_name,
		route_manager=route_manager,
		priority=priority,
		caller=caller,
		caller_phone=caller_phone,
		vehicle=vehicle,
		url=url,
	)


def send_driver_ack(issue_name, call_log):
	"""Confirm to the caller that their voicemail was logged. Email and
	Exotel WhatsApp are gated independently; each is skipped silently
	when its contact point cannot be resolved."""
	settings = frappe.get_single("Voice Ops Settings")
	if not getattr(settings, "enable_driver_ack", 0):
		return

	caller = call_log.get("caller_name") or ""
	subject_line = frappe.db.get_value("Issue", issue_name, "subject") or ""
	url = get_url(f"/app/issue/{issue_name}")
	company = _company_name()

	email = (call_log.get("caller_email") or "").strip()
	if email:
		subject = f"[{company}] Voicemail received · {issue_name}"
		html = _render_driver_ack_html(
			company=company,
			issue_name=issue_name,
			issue_subject=subject_line,
			caller=caller,
			url=url,
		)
		try:
			frappe.sendmail(recipients=[email], subject=subject, message=html)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Driver ack email failed for {issue_name}",
			)

	_send_driver_whatsapp(issue_name=issue_name, call_log=call_log, caller=caller)


def _send_driver_whatsapp(*, issue_name, call_log, caller):
	template = (frappe.db.get_single_value(
		"Voice Ops Settings", "exotel_whatsapp_template_driver_ack"
	) or "").strip()
	if not template:
		return

	to_phone = _normalize_phone(call_log.get("from"))
	if not to_phone:
		return

	params = [caller or "there", issue_name]
	try:
		send_exotel_whatsapp(to_phone=to_phone, template_name=template, template_params=params)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Driver WhatsApp ack failed for {issue_name}",
		)


def _send_route_manager_whatsapp(
	*, issue_name, route_manager, priority, caller, caller_phone, vehicle, url
):
	template = (frappe.db.get_single_value(
		"Voice Ops Settings", "exotel_whatsapp_template_route_manager_alert"
	) or "").strip()
	if not template:
		return

	to_phone = _normalize_phone((route_manager or {}).get("phone"))
	if not to_phone:
		return

	manager_name = (route_manager or {}).get("name") or "Team"
	caller_display = f"{caller} · {caller_phone}" if caller_phone and caller_phone != "-" else caller
	params = [manager_name, issue_name, priority, caller_display, vehicle, url]
	try:
		send_exotel_whatsapp(to_phone=to_phone, template_name=template, template_params=params)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Route manager WhatsApp alert failed for {issue_name}",
		)


def _normalize_phone(raw):
	"""Return a +E.164-ish string, or empty. Assumes IN default when the
	number is a bare 10-digit local."""
	value = (raw or "").strip().replace(" ", "").replace("-", "")
	if not value:
		return ""
	if value.startswith("+"):
		return value
	digits = "".join(ch for ch in value if ch.isdigit())
	if len(digits) == 10:
		return f"+91{digits}"
	if len(digits) == 12 and digits.startswith("91"):
		return f"+{digits}"
	if digits:
		return f"+{digits}"
	return ""


def _company_name():
	try:
		return (frappe.db.get_single_value("FMS AI Settings", "company_name") or "").strip() or "Voice Ops"
	except Exception:
		return "Voice Ops"


def _truncate(text, limit):
	text = (text or "").strip()
	if len(text) <= limit:
		return text
	return text[: limit - 1].rstrip() + "…"


def _render_driver_ack_html(*, company, issue_name, issue_subject, caller, url):
	greeting = f"Hi {caller}," if caller else "Hi,"
	subject_block = ""
	if issue_subject:
		subject_block = (
			f'<p style="margin:0 0 16px 0;color:#334155;font-size:14px;line-height:1.6;">'
			f'We noted the concern as: <em style="color:#0f172a;">“{issue_subject}”</em>.'
			"</p>"
		)

	return f"""
<div style="background:#f8fafc;padding:24px 12px;font-family:-apple-system,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
    <div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#ffffff;padding:22px 28px;">
      <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.85;">{company}</div>
      <div style="font-size:18px;font-weight:600;margin-top:4px;">Voicemail received</div>
    </div>
    <div style="padding:24px 28px;">
      <p style="margin:0 0 12px 0;color:#0f172a;font-size:15px;">{greeting}</p>
      <p style="margin:0 0 16px 0;color:#334155;font-size:14px;line-height:1.6;">
        We have received your voicemail and logged it as ticket
        <strong style="color:#0f172a;">{issue_name}</strong>. Our operations team
        will review it and follow up shortly.
      </p>
      {subject_block}
      <p style="margin:20px 0 0 0;color:#64748b;font-size:12px;">
        If this was urgent, please call your route manager directly.
      </p>
    </div>
    <div style="padding:14px 28px;background:#f8fafc;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
      Automated notification · {company} Voice Ops
    </div>
  </div>
</div>
"""


def _render_route_manager_html(
	*, company, issue_name, issue_subject, priority, caller, phone, vehicle,
	excerpt, url, manager_name
):
	priority_color = {
		"High": "#b91c1c",
		"Medium": "#b45309",
		"Low": "#166534",
	}.get(priority, "#334155")
	greeting = f"Hi {manager_name}," if manager_name else "Hi,"

	return f"""
<div style="background:#f8fafc;padding:24px 12px;font-family:-apple-system,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
    <div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#ffffff;padding:22px 28px;">
      <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.85;">{company} · Voice Ops</div>
      <div style="font-size:18px;font-weight:600;margin-top:4px;">New Issue raised from voicemail</div>
    </div>

    <div style="padding:22px 28px 4px 28px;">
      <p style="margin:0 0 14px 0;color:#0f172a;font-size:15px;">{greeting}</p>
      <p style="margin:0 0 18px 0;color:#334155;font-size:14px;line-height:1.55;">
        A new Issue has been auto-created from an incoming voicemail on your route.
      </p>

      <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:8px;">
        <tr>
          <td style="padding:8px 10px;background:#f1f5f9;color:#475569;font-weight:600;width:32%;border:1px solid #e2e8f0;">Ticket</td>
          <td style="padding:8px 10px;color:#0f172a;border:1px solid #e2e8f0;"><a href="{url}" style="color:#2563eb;text-decoration:none;font-weight:600;">{issue_name}</a></td>
        </tr>
        <tr>
          <td style="padding:8px 10px;background:#f1f5f9;color:#475569;font-weight:600;border:1px solid #e2e8f0;">Subject</td>
          <td style="padding:8px 10px;color:#0f172a;border:1px solid #e2e8f0;">{issue_subject}</td>
        </tr>
        <tr>
          <td style="padding:8px 10px;background:#f1f5f9;color:#475569;font-weight:600;border:1px solid #e2e8f0;">Priority</td>
          <td style="padding:8px 10px;color:#0f172a;border:1px solid #e2e8f0;">
            <span style="display:inline-block;padding:2px 10px;border-radius:10px;background:{priority_color};color:#ffffff;font-size:12px;font-weight:600;">{priority}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 10px;background:#f1f5f9;color:#475569;font-weight:600;border:1px solid #e2e8f0;">Caller</td>
          <td style="padding:8px 10px;color:#0f172a;border:1px solid #e2e8f0;">{caller} · {phone}</td>
        </tr>
        <tr>
          <td style="padding:8px 10px;background:#f1f5f9;color:#475569;font-weight:600;border:1px solid #e2e8f0;">Vehicle</td>
          <td style="padding:8px 10px;color:#0f172a;border:1px solid #e2e8f0;">{vehicle}</td>
        </tr>
      </table>
    </div>

    <div style="padding:6px 28px 24px 28px;">
      <div style="background:#eff6ff;border-left:4px solid #2563eb;border-radius:4px;padding:14px 16px;margin-top:14px;">
        <div style="font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#2563eb;margin-bottom:6px;">Transcript excerpt</div>
        <div style="color:#1e293b;font-size:13px;line-height:1.6;white-space:pre-wrap;">{excerpt or '(no transcript)'}</div>
      </div>

      <div style="margin-top:22px;text-align:center;">
        <a href="{url}" style="display:inline-block;padding:10px 22px;background:#2563eb;color:#ffffff;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;">Open ticket in ERPNext</a>
      </div>
    </div>

    <div style="padding:14px 28px;background:#f8fafc;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
      Automated notification · {company} Voice Ops
    </div>
  </div>
</div>
"""
