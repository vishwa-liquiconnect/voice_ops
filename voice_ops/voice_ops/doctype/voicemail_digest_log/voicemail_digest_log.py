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
	rows_html = ""
	for i, row in enumerate(doc.voicemails, 1):
		url = get_url(f"/app/call-log/{row.call_log}")
		time_str = format_datetime(row.call_time, "HH:mm") if row.call_time else ""
		rows_html += f"""
		<tr>
			<td style="padding:8px;border:1px solid #ddd;">{i}</td>
			<td style="padding:8px;border:1px solid #ddd;">{row.caller_name or '-'}</td>
			<td style="padding:8px;border:1px solid #ddd;">{row.mobile or '-'}</td>
			<td style="padding:8px;border:1px solid #ddd;">{row.designation or '-'}</td>
			<td style="padding:8px;border:1px solid #ddd;">{row.vehicle or '-'}</td>
			<td style="padding:8px;border:1px solid #ddd;">{time_str}</td>
			<td style="padding:8px;border:1px solid #ddd;">{row.transcript or 'No transcript'}</td>
			<td style="padding:8px;border:1px solid #ddd;"><a href="{url}">View</a></td>
		</tr>"""

	overall_html = ""
	if (doc.overall_summary or "").strip():
		overall_html = f"""
		<div style="background:#f5faff;border-left:4px solid #2b6cb0;padding:12px 16px;margin:12px 0 20px;">
			<div style="font-weight:600;margin-bottom:6px;">Overall Summary</div>
			<div style="white-space:pre-wrap;">{doc.overall_summary}</div>
		</div>
		"""

	suffix = "s" if doc.voicemail_count != 1 else ""
	return f"""
		<h3>Voicemail Digest - {today}</h3>
		<p>{doc.voicemail_count} voicemail{suffix} received.</p>
		{overall_html}
		<table style="border-collapse:collapse;width:100%;">
			<tr style="background:#f5f5f5;">
				<th style="padding:8px;border:1px solid #ddd;">#</th>
				<th style="padding:8px;border:1px solid #ddd;">Name</th>
				<th style="padding:8px;border:1px solid #ddd;">Mobile</th>
				<th style="padding:8px;border:1px solid #ddd;">Designation</th>
				<th style="padding:8px;border:1px solid #ddd;">Vehicle</th>
				<th style="padding:8px;border:1px solid #ddd;">Time</th>
				<th style="padding:8px;border:1px solid #ddd;">Transcript</th>
				<th style="padding:8px;border:1px solid #ddd;">Link</th>
			</tr>
			{rows_html}
		</table>
	"""


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
