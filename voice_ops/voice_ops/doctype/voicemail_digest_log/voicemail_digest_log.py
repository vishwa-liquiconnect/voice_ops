import frappe
from frappe.model.document import Document
from frappe.utils import format_datetime, get_datetime, get_url, now_datetime


class VoicemailDigestLog(Document):
	def validate(self):
		if self.window_start and self.window_end:
			if get_datetime(self.window_end) <= get_datetime(self.window_start):
				frappe.throw("Window End must be after Window Start.")

	@frappe.whitelist()
	def generate(self):
		"""Scan Call Logs in the window, build entries + overall summary +
		rendered message body. Does not send anything."""
		voicemails = _fetch_voicemails(self.window_start, self.window_end)

		self.voicemails = []
		for vm in voicemails:
			caller = _enrich_voicemail(vm)
			self.append("voicemails", {
				"call_log": vm.name,
				"caller_name": caller.get("name") or "",
				"mobile": vm.get("from") or "",
				"designation": caller.get("designation") or "",
				"vehicle": caller.get("vehicle_label") or "",
				"call_time": vm.get("creation"),
				"transcript": vm.get("summary") or "",
			})

		self.voicemail_count = len(self.voicemails)

		enriched = _rows_for_summary(self.voicemails)
		try:
			from voice_ops.services.summarizer import summarize_voicemail_digest

			self.overall_summary = summarize_voicemail_digest(enriched) or ""
		except Exception:
			self.overall_summary = ""

		today = now_datetime().strftime("%Y-%m-%d")
		suffix = "s" if self.voicemail_count != 1 else ""
		if not self.subject:
			self.subject = f"[Voice Ops] Voicemail Digest - {today} ({self.voicemail_count} voicemail{suffix})"

		self.message_body = _render_html_body(self, today)
		self.status = "Generated"
		self.save(ignore_permissions=True)
		frappe.msgprint(
			f"Generated digest with {self.voicemail_count} voicemail{suffix}.",
			indicator="green",
			alert=True,
		)

	@frappe.whitelist()
	def send_now(self):
		"""Send the already-generated digest to the configured recipients.
		Re-runs generate() first if the doc hasn't been generated yet."""
		if self.status == "Draft" or not self.message_body:
			self.generate()
			self.reload()

		channel = self.channel or "Email"
		today = now_datetime().strftime("%Y-%m-%d")

		errors = []
		if channel in ("Email", "Both"):
			try:
				_send_email(self, today)
			except Exception:
				errors.append("email")
				frappe.log_error(
					frappe.get_traceback(),
					f"Voice Ops: Digest email failed for {self.name}",
				)

		if channel in ("WhatsApp", "Both"):
			try:
				_send_whatsapp(self, today)
			except Exception:
				errors.append("whatsapp")
				frappe.log_error(
					frappe.get_traceback(),
					f"Voice Ops: Digest WhatsApp failed for {self.name}",
				)

		if errors:
			self.status = "Failed"
		else:
			self.status = "Sent"
			self.sent_at = now_datetime()

		self.save(ignore_permissions=True)
		frappe.msgprint(
			f"Digest {self.name} status: {self.status}",
			indicator="red" if errors else "green",
			alert=True,
		)


def _fetch_voicemails(window_start, window_end):
	return frappe.db.sql(
		"""
		SELECT name, `from`, `to`, start_time, duration, summary, recording_url, creation
		FROM `tabCall Log`
		WHERE type_of_call = 'Voicemail'
		  AND creation >= %s AND creation < %s
		ORDER BY creation ASC
		""",
		(window_start, window_end),
		as_dict=True,
	)


def _enrich_voicemail(vm):
	try:
		from voice_ops.services.caller_lookup import read_call_log_context, resolve_caller

		info = read_call_log_context(vm.get("name")) or {}
		if not (info.get("employee") or info.get("contact") or info.get("vehicle")):
			info = resolve_caller(vm.get("from")) or {}
		return info or {}
	except Exception:
		return {}


def _rows_for_summary(entries):
	"""Adapt the child-table rows into the shape summarize_voicemail_digest
	expects (dict keys: from, caller_name, caller_designation, caller_vehicle,
	creation, summary)."""
	out = []
	for row in entries:
		out.append({
			"from": row.mobile,
			"caller_name": row.caller_name,
			"caller_designation": row.designation,
			"caller_vehicle": row.vehicle,
			"creation": row.call_time,
			"summary": row.transcript,
		})
	return out


def _render_html_body(doc, today):
	company = _digest_company_name()
	suffix = "s" if doc.voicemail_count != 1 else ""
	window_label = _format_window(doc.window_start, doc.window_end)

	rows_html = ""
	for i, row in enumerate(doc.voicemails, 1):
		url = get_url(f"/app/call-log/{row.call_log}")
		time_str = format_datetime(row.call_time, "dd MMM, HH:mm") if row.call_time else "-"
		caller_cell = row.caller_name or "Unknown"
		if row.designation:
			caller_cell += f'<div style="color:#64748b;font-size:11px;margin-top:2px;">{row.designation}</div>'
		mobile_cell = row.mobile or "-"
		vehicle_cell = row.vehicle or "-"
		transcript_cell = (row.transcript or "(no transcript)").replace("\n", "<br>")

		rows_html += f"""
		<tr>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#94a3b8;font-size:12px;vertical-align:top;">{i}</td>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#0f172a;font-size:13px;vertical-align:top;font-weight:500;">{caller_cell}</td>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#334155;font-size:13px;vertical-align:top;">{mobile_cell}</td>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#334155;font-size:13px;vertical-align:top;">{vehicle_cell}</td>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#64748b;font-size:12px;vertical-align:top;white-space:nowrap;">{time_str}</td>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;color:#1e293b;font-size:13px;line-height:1.55;vertical-align:top;">{transcript_cell}</td>
			<td style="padding:12px 10px;border-bottom:1px solid #f1f5f9;font-size:12px;vertical-align:top;"><a href="{url}" style="color:#2563eb;text-decoration:none;font-weight:600;">Open →</a></td>
		</tr>"""

	overall_block = ""
	if (doc.overall_summary or "").strip():
		summary_html = doc.overall_summary.replace("\n", "<br>")
		overall_block = f"""
		<div style="padding:0 28px;">
			<div style="background:#eff6ff;border-left:4px solid #2563eb;border-radius:4px;padding:16px 20px;margin:4px 0 20px 0;">
				<div style="font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#2563eb;margin-bottom:8px;">Overall Summary</div>
				<div style="color:#1e293b;font-size:14px;line-height:1.6;">{summary_html}</div>
			</div>
		</div>
		"""

	return f"""
<div style="background:#f8fafc;padding:24px 12px;font-family:-apple-system,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:760px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">

    <div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#ffffff;padding:26px 30px;">
      <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.85;">{company} · Voice Ops</div>
      <div style="font-size:22px;font-weight:600;margin-top:6px;letter-spacing:-0.01em;">Voicemail Digest</div>
      <div style="font-size:13px;opacity:0.9;margin-top:4px;">{today} · {window_label}</div>
    </div>

    <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background:#f1f5f9;border-bottom:1px solid #e2e8f0;">
      <tr>
        <td style="padding:18px 30px;width:50%;border-right:1px solid #e2e8f0;">
          <div style="font-size:26px;font-weight:700;color:#0f172a;line-height:1;">{doc.voicemail_count}</div>
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#64748b;margin-top:6px;">Voicemail{suffix}</div>
        </td>
        <td style="padding:18px 30px;width:50%;">
          <div style="font-size:13px;font-weight:600;color:#0f172a;">{window_label}</div>
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#64748b;margin-top:6px;">Window</div>
        </td>
      </tr>
    </table>

    {overall_block}

    <div style="padding:0 28px 12px 28px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
        <thead>
          <tr>
            <th style="padding:10px 10px;text-align:left;background:#f8fafc;color:#475569;font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:2px solid #e2e8f0;">#</th>
            <th style="padding:10px 10px;text-align:left;background:#f8fafc;color:#475569;font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:2px solid #e2e8f0;">Caller</th>
            <th style="padding:10px 10px;text-align:left;background:#f8fafc;color:#475569;font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:2px solid #e2e8f0;">Mobile</th>
            <th style="padding:10px 10px;text-align:left;background:#f8fafc;color:#475569;font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:2px solid #e2e8f0;">Vehicle</th>
            <th style="padding:10px 10px;text-align:left;background:#f8fafc;color:#475569;font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:2px solid #e2e8f0;">Time</th>
            <th style="padding:10px 10px;text-align:left;background:#f8fafc;color:#475569;font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:2px solid #e2e8f0;">Transcript</th>
            <th style="padding:10px 10px;background:#f8fafc;border-bottom:2px solid #e2e8f0;"></th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <div style="padding:16px 30px;background:#f8fafc;color:#94a3b8;font-size:11px;text-align:center;border-top:1px solid #e2e8f0;">
      Automated digest · {company} Voice Ops · {today}
    </div>
  </div>
</div>
"""


def _digest_company_name():
	try:
		return (frappe.db.get_single_value("FMS AI Settings", "company_name") or "").strip() or "Voice Ops"
	except Exception:
		return "Voice Ops"


def _format_window(start, end):
	if not start or not end:
		return ""
	try:
		return f"{format_datetime(start, 'dd MMM, HH:mm')} → {format_datetime(end, 'dd MMM, HH:mm')}"
	except Exception:
		return f"{start} → {end}"


def _parse_list(raw):
	if not raw:
		return []
	return [p.strip() for p in str(raw).split(",") if p.strip()]


def _send_email(doc, today):
	recipients = _parse_list(doc.email_recipients)
	if not recipients:
		return

	attachments = []
	try:
		from frappe.utils.pdf import get_pdf

		pdf_content = get_pdf(doc.message_body)
		attachments.append({
			"fname": f"voicemail-digest-{today}.pdf",
			"fcontent": pdf_content,
		})
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Voice Ops: Digest PDF failed for {doc.name}")

	frappe.sendmail(
		recipients=recipients,
		subject=doc.subject,
		message=doc.message_body,
		attachments=attachments or None,
	)


def _send_whatsapp(doc, today):
	phones = _parse_list(doc.whatsapp_recipients)
	if not phones:
		return

	from voice_ops.services.telephony import send_whatsapp

	lines = [
		f"*Voicemail Digest* ({doc.voicemail_count} voicemail{'s' if doc.voicemail_count != 1 else ''})",
		f"Date: {today}",
		"",
	]
	if (doc.overall_summary or "").strip():
		lines.extend(["*Overall Summary*", doc.overall_summary, ""])

	display = (doc.voicemails or [])[:20]
	for i, row in enumerate(display, 1):
		time_str = format_datetime(row.call_time, "HH:mm") if row.call_time else ""
		name = row.caller_name or "Unknown"
		mobile = row.mobile or "-"
		designation = row.designation or "-"
		vehicle = row.vehicle or "-"
		excerpt = (row.transcript or "No transcript")[:100]
		lines.append(f"{i}. {name} ({designation}) | {mobile} | Veh: {vehicle} | {time_str}")
		lines.append(f"   {excerpt}")
		lines.append("")

	if doc.voicemail_count > 20:
		lines.append(f"... and {doc.voicemail_count - 20} more. Check email for full digest.")

	message = "\n".join(lines)
	for phone in phones:
		try:
			send_whatsapp(phone, message)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Digest WhatsApp failed for {phone} on {doc.name}",
			)
