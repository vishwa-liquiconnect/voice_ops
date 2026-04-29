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

from voice_ops.services.telephony import send_twilio_whatsapp_template


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


def send_issue_alert_email(issue_name, call_log):
	"""Email the configured Issue Alert recipients (e.g. management) every
	time a new Issue is created from a call. Best-effort; silent when the
	toggle is off or no recipients are configured."""
	settings = frappe.get_single("Voice Ops Settings")
	if not getattr(settings, "enable_issue_email_alert", 0):
		return

	recipients = _parse_recipients(getattr(settings, "issue_alert_email", ""))
	if not recipients:
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

	subject = f"[{company}] New Issue {issue_name} · {priority}"
	html = _render_issue_alert_html(
		company=company,
		issue_name=issue_name,
		issue_subject=subject_line,
		priority=priority,
		caller=caller,
		phone=caller_phone,
		vehicle=vehicle,
		excerpt=excerpt,
		url=url,
	)
	try:
		frappe.sendmail(recipients=recipients, subject=subject, message=html)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Issue alert email failed for {issue_name}",
		)


def _parse_recipients(raw):
	if not raw:
		return []
	parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
	return [p for p in parts if p and "@" in p]


def _send_driver_whatsapp(*, issue_name, call_log, caller):
	if not frappe.db.get_single_value("Voice Ops Settings", "enable_exotel_whatsapp_ack"):
		return

	content_sid = _resolve_driver_whatsapp_content_sid(call_log)
	if not content_sid:
		return

	to_phone = _normalize_phone(call_log.get("from"))
	if not to_phone:
		return

	variables = {"1": caller or "there", "2": issue_name}
	try:
		send_twilio_whatsapp_template(
			to_phone=to_phone,
			content_sid=content_sid,
			content_variables=variables,
			reference_doctype="Issue",
			reference_docname=issue_name,
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Driver WhatsApp ack failed for {issue_name}",
		)


def _resolve_driver_whatsapp_content_sid(call_log):
	"""Pick the driver-ack Twilio Content SID by the caller's detected language.

	Priority:
	1. A row in Voice Ops Settings.language_template_map whose `language_code`
	   matches the Call Log's `custom_detected_language`.
	2. The single exotel_whatsapp_template_driver_ack setting as a generic
	   fallback template name.

	In both cases the template *name* is looked up in WhatsApp Template
	Reference to resolve the actual Twilio Content SID. Returns the SID
	string (HX...) or None when no usable template is configured.
	"""
	detected = (call_log.get("custom_detected_language") or "").strip()
	settings = frappe.get_single("Voice Ops Settings")

	template_name = ""
	if detected:
		for row in (settings.get("language_template_map") or []):
			if (row.language_code or "").strip() == detected:
				template_name = (row.whatsapp_template or "").strip()
				break

	if not template_name:
		template_name = (settings.exotel_whatsapp_template_driver_ack or "").strip()

	return _content_sid_from_template_name(template_name)


def _send_route_manager_whatsapp(
	*, issue_name, route_manager, priority, caller, caller_phone, vehicle, url
):
	if not frappe.db.get_single_value("Voice Ops Settings", "enable_exotel_whatsapp_ack"):
		return

	template_name = (frappe.db.get_single_value(
		"Voice Ops Settings", "exotel_whatsapp_template_route_manager_alert"
	) or "").strip()
	content_sid = _content_sid_from_template_name(template_name)
	if not content_sid:
		return

	to_phone = _normalize_phone((route_manager or {}).get("phone"))
	if not to_phone:
		return

	manager_name = (route_manager or {}).get("name") or "Team"
	caller_display = f"{caller} · {caller_phone}" if caller_phone and caller_phone != "-" else caller
	variables = {
		"1": manager_name,
		"2": issue_name,
		"3": priority,
		"4": caller_display,
		"5": vehicle,
		"6": url,
	}
	try:
		send_twilio_whatsapp_template(
			to_phone=to_phone,
			content_sid=content_sid,
			content_variables=variables,
			reference_doctype="Issue",
			reference_docname=issue_name,
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Route manager WhatsApp alert failed for {issue_name}",
		)


def _content_sid_from_template_name(template_name):
	"""Look up a Twilio Content SID from a WhatsApp Template Reference doc.

	Returns None when the doctype isn't installed (twilio_integration
	absent) or the reference doesn't exist.
	"""
	if not template_name:
		return None
	if not frappe.db.exists("DocType", "WhatsApp Template Reference"):
		return None
	sid = frappe.db.get_value(
		"WhatsApp Template Reference", template_name, "content_sid"
	)
	return (sid or "").strip() or None


def _normalize_phone(raw):
	"""Return a +E.164-ish string, or empty. Assumes IN default when the
	number is a bare 10-digit local. Strips the Indian trunk prefix 0 so
	e.g. '06382741676' becomes '+916382741676' instead of '+06382741676'."""
	value = (raw or "").strip().replace(" ", "").replace("-", "")
	if not value:
		return ""
	if value.startswith("+"):
		return value
	digits = "".join(ch for ch in value if ch.isdigit())
	if digits.startswith("0") and len(digits) == 11:
		digits = digits[1:]
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


def _render_issue_alert_html(
	*, company, issue_name, issue_subject, priority, caller, phone, vehicle,
	excerpt, url
):
	priority_color = {
		"High": "#b91c1c",
		"Medium": "#b45309",
		"Low": "#166534",
	}.get(priority, "#0f172a")

	excerpt_html = (excerpt or "(no transcript)").replace("\n", "<br>")

	row = (
		'<tr>'
		'<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#64748b;font-size:12px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;width:30%;vertical-align:top;">{label}</td>'
		'<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#0f172a;font-size:13px;line-height:1.55;vertical-align:top;">{value}</td>'
		'</tr>'
	)
	rows_html = "".join([
		row.format(label="Subject", value=issue_subject),
		row.format(label="Caller", value=f"{caller} &middot; {phone}"),
		row.format(label="Vehicle", value=vehicle),
	])

	return f"""
<div style="background:#f8fafc;padding:24px 12px;font-family:-apple-system,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">

    <div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#ffffff;padding:26px 30px;">
      <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.85;">{company} · Voice Ops</div>
      <div style="font-size:22px;font-weight:600;margin-top:6px;letter-spacing:-0.01em;">New Issue Raised</div>
      <div style="font-size:13px;opacity:0.9;margin-top:4px;">{issue_name}</div>
    </div>

    <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background:#f1f5f9;border-bottom:1px solid #e2e8f0;">
      <tr>
        <td style="padding:18px 30px;width:50%;border-right:1px solid #e2e8f0;">
          <div style="font-size:22px;font-weight:700;color:{priority_color};line-height:1;">{priority}</div>
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#64748b;margin-top:6px;">Priority</div>
        </td>
        <td style="padding:18px 30px;width:50%;">
          <div style="font-size:13px;font-weight:600;color:#0f172a;">{issue_name}</div>
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#64748b;margin-top:6px;">Ticket</div>
        </td>
      </tr>
    </table>

    <div style="padding:0 28px;">
      <div style="background:#eff6ff;border-left:4px solid #2563eb;border-radius:4px;padding:16px 20px;margin:20px 0 8px 0;">
        <div style="font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#2563eb;margin-bottom:8px;">Transcript Excerpt</div>
        <div style="color:#1e293b;font-size:14px;line-height:1.6;">{excerpt_html}</div>
      </div>
    </div>

    <div style="padding:0 28px 12px 28px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <div style="padding:8px 28px 24px 28px;text-align:center;">
      <a href="{url}" style="display:inline-block;padding:10px 22px;background:#2563eb;color:#ffffff;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;">Open ticket &rarr;</a>
    </div>

    <div style="padding:16px 30px;background:#f8fafc;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
      Automated alert · {company} Voice Ops
    </div>
  </div>
</div>
"""
