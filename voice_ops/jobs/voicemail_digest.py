"""
Voicemail Digest Job

Scheduler entry point that runs every 5 minutes (via hooks.py cron).
Creates a `Voicemail Digest Log` for the current window and invokes
its generate + send pipeline, which is the same code path used by
manually-created digest logs. One implementation of the digest
lives on the doctype; this file just decides *when* to trigger it.
"""

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime


def check_and_send():
	if not frappe.db.get_single_value("Voice Ops Settings", "enabled"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	if not settings.enable_voicemail_digest:
		return

	if not _is_digest_time(settings):
		return

	window_start = (
		get_datetime(settings.last_voicemail_digest_sent)
		if settings.last_voicemail_digest_sent
		else add_to_date(now_datetime(), hours=-24)
	)
	window_end = now_datetime()

	# Update timestamp first — keeps the 23h lockout intact even if the
	# digest run below hits a snag and retries next cron tick.
	frappe.db.set_single_value("Voice Ops Settings", "last_voicemail_digest_sent", window_end)
	frappe.db.commit()

	log = frappe.get_doc({
		"doctype": "Voicemail Digest Log",
		"window_start": window_start,
		"window_end": window_end,
		"triggered_by": "Scheduled",
		"channel": settings.voicemail_digest_channel or "Email",
		"email_recipients": settings.voicemail_digest_email or "",
		"whatsapp_recipients": settings.voicemail_digest_phone or "",
	})
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	log.generate()
	if log.voicemail_count == 0:
		return

	log.send_now()


def _is_digest_time(settings):
	now = now_datetime()
	configured_hour = int((settings.voicemail_digest_time or "10:00").split(":")[0])

	if now.hour != configured_hour:
		return False

	if settings.last_voicemail_digest_sent:
		last_sent = get_datetime(settings.last_voicemail_digest_sent)
		if (now - last_sent).total_seconds() < 23 * 3600:
			return False

	return True
