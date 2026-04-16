"""
Voicemail Digest Job

Scheduler job that runs every 5 minutes (via hooks.py cron).
Checks if the configured digest time has arrived and if so,
fetches voicemails since the last digest, compiles a summary,
and sends via email/WhatsApp per settings.
"""

import frappe
from frappe.utils import now_datetime, get_datetime, add_to_date, get_url, format_datetime


def check_and_send():
	"""Scheduler entry point. Checks if it's time to send the digest."""
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	if not settings.enable_voicemail_digest:
		return

	if not _is_digest_time(settings):
		return

	since_dt = get_datetime(settings.last_voicemail_digest_sent) if settings.last_voicemail_digest_sent else add_to_date(now_datetime(), hours=-24)
	voicemails = _fetch_voicemails(since_dt)

	# Update timestamp regardless of whether there are voicemails
	frappe.db.set_single_value("Voice Ops Settings", "last_voicemail_digest_sent", now_datetime())
	frappe.db.commit()

	if not voicemails:
		return

	from voice_ops.services.summarizer import summarize_voicemail_digest
	overall_summary = summarize_voicemail_digest(voicemails)

	channel = settings.voicemail_digest_channel or "Email"

	if channel in ("Email", "Both"):
		_send_email_digest(settings, voicemails, overall_summary)

	if channel in ("WhatsApp", "Both"):
		_send_whatsapp_digest(settings, voicemails, overall_summary)


def _is_digest_time(settings):
	"""Check if current hour matches configured digest time and hasn't been sent today."""
	now = now_datetime()
	configured_hour = int((settings.voicemail_digest_time or "10:00").split(":")[0])

	if now.hour != configured_hour:
		return False

	if settings.last_voicemail_digest_sent:
		last_sent = get_datetime(settings.last_voicemail_digest_sent)
		if (now - last_sent).total_seconds() < 23 * 3600:
			return False

	return True


def _fetch_voicemails(since_dt):
	"""Fetch voicemail Call Logs since the given datetime."""
	return frappe.db.sql("""
		SELECT name, `from`, `to`, start_time, duration, summary, recording_url, creation
		FROM `tabCall Log`
		WHERE type_of_call = 'Voicemail'
		AND creation >= %s
		ORDER BY creation ASC
	""", (since_dt,), as_dict=True)


def _send_email_digest(settings, voicemails, overall_summary=""):
	"""Send voicemail digest via email."""
	recipients = [e.strip() for e in (settings.voicemail_digest_email or "").split(",") if e.strip()]
	if not recipients:
		return

	today = now_datetime().strftime("%Y-%m-%d")
	subject = f"[Voice Ops] Voicemail Digest - {today} ({len(voicemails)} voicemail{'s' if len(voicemails) != 1 else ''})"

	email_rows = ""
	pdf_rows = ""
	for i, vm in enumerate(voicemails, 1):
		call_url = get_url(f"/app/call-log/{vm.name}")
		time_str = format_datetime(vm.creation, "HH:mm") if vm.creation else ""
		summary = vm.summary or "No transcript"
		caller = vm.get("from") or "Unknown"
		email_rows += f"""
		<tr>
			<td style="padding: 8px; border: 1px solid #ddd;">{i}</td>
			<td style="padding: 8px; border: 1px solid #ddd;">{caller}</td>
			<td style="padding: 8px; border: 1px solid #ddd;">{time_str}</td>
			<td style="padding: 8px; border: 1px solid #ddd;">{summary}</td>
			<td style="padding: 8px; border: 1px solid #ddd;"><a href="{call_url}">View</a></td>
		</tr>"""
		pdf_rows += f"""
		<tr>
			<td style="padding: 8px; border: 1px solid #ddd;">{i}</td>
			<td style="padding: 8px; border: 1px solid #ddd;">{caller}</td>
			<td style="padding: 8px; border: 1px solid #ddd;">{time_str}</td>
			<td style="padding: 8px; border: 1px solid #ddd;">{summary}</td>
			<td style="padding: 8px; border: 1px solid #ddd; word-break: break-all;">{call_url}</td>
		</tr>"""

	overall_html = ""
	if overall_summary:
		overall_html = f"""
		<div style="background: #f5faff; border-left: 4px solid #2b6cb0; padding: 12px 16px; margin: 12px 0 20px;">
			<div style="font-weight: 600; margin-bottom: 6px;">Overall Summary</div>
			<div style="white-space: pre-wrap;">{overall_summary}</div>
		</div>
		"""

	message = f"""
	<h3>Voicemail Digest - {today}</h3>
	<p>{len(voicemails)} voicemail{'s' if len(voicemails) != 1 else ''} received.</p>
	{overall_html}
	<table style="border-collapse: collapse; width: 100%;">
		<tr style="background: #f5f5f5;">
			<th style="padding: 8px; border: 1px solid #ddd;">#</th>
			<th style="padding: 8px; border: 1px solid #ddd;">Caller</th>
			<th style="padding: 8px; border: 1px solid #ddd;">Time</th>
			<th style="padding: 8px; border: 1px solid #ddd;">Transcript</th>
			<th style="padding: 8px; border: 1px solid #ddd;">Link</th>
		</tr>
		{email_rows}
	</table>
	"""

	pdf_html = f"""
	<html>
	<head><meta charset="utf-8"><title>Voicemail Digest - {today}</title></head>
	<body style="font-family: Arial, sans-serif;">
		<h2>Voicemail Digest - {today}</h2>
		<p>{len(voicemails)} voicemail{'s' if len(voicemails) != 1 else ''} received.</p>
		{overall_html}
		<table style="border-collapse: collapse; width: 100%; font-size: 12px;">
			<thead>
				<tr style="background: #f5f5f5;">
					<th style="padding: 8px; border: 1px solid #ddd;">#</th>
					<th style="padding: 8px; border: 1px solid #ddd;">Caller</th>
					<th style="padding: 8px; border: 1px solid #ddd;">Time</th>
					<th style="padding: 8px; border: 1px solid #ddd;">Transcript</th>
					<th style="padding: 8px; border: 1px solid #ddd;">Link</th>
				</tr>
			</thead>
			<tbody>
				{pdf_rows}
			</tbody>
		</table>
	</body>
	</html>
	"""

	attachments = []
	try:
		from frappe.utils.pdf import get_pdf
		pdf_content = get_pdf(pdf_html)
		attachments.append({
			"fname": f"voicemail-digest-{today}.pdf",
			"fcontent": pdf_content,
		})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Voicemail Digest PDF Generation Failed")

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			attachments=attachments or None,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Voicemail Digest Email Failed")


def _send_whatsapp_digest(settings, voicemails, overall_summary=""):
	"""Send voicemail digest via WhatsApp."""
	phones = [p.strip() for p in (settings.voicemail_digest_phone or "").split(",") if p.strip()]
	if not phones:
		return

	from voice_ops.services.telephony import send_whatsapp

	today = now_datetime().strftime("%Y-%m-%d")
	lines = [f"*Voicemail Digest* ({len(voicemails)} voicemail{'s' if len(voicemails) != 1 else ''})", f"Date: {today}", ""]

	if overall_summary:
		lines.append("*Overall Summary*")
		lines.append(overall_summary)
		lines.append("")

	# Truncate to 20 for WhatsApp message size limits
	display = voicemails[:20]
	for i, vm in enumerate(display, 1):
		time_str = format_datetime(vm.creation, "HH:mm") if vm.creation else ""
		summary = (vm.summary or "No transcript")[:100]
		lines.append(f"{i}. From: {vm.get('from') or 'Unknown'} | {time_str} | {vm.duration or 0}s")
		lines.append(f"   {summary}")
		lines.append("")

	if len(voicemails) > 20:
		lines.append(f"... and {len(voicemails) - 20} more. Check email for full digest.")

	message = "\n".join(lines)

	for phone in phones:
		try:
			send_whatsapp(phone, message)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Voice Ops: Voicemail Digest WhatsApp Failed for {phone}")
