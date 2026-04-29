"""
Call Recording Retention

Daily scheduler that deletes Call Log recording files older than
RETENTION_DAYS. Bounds storage growth (S3 or local disk, depending
on the File backend) without affecting transcripts, summaries, or
Issue records — only the playable audio is removed once it's
unlikely to be reviewed.

Targets every File whose `file_name` starts with `call_recording_`
attached to either Call Log or Twilio Call Log, regardless of
which provider (Twilio / Exotel) ingested it.
"""

import frappe
from frappe.utils import add_to_date, now_datetime


RETENTION_DAYS = 30


def purge_old_recordings():
	"""Delete Call Log recording files older than RETENTION_DAYS.

	Best-effort: per-file failures are logged and don't abort the
	batch. Uses `frappe.delete_doc` on the File row so the underlying
	backend (S3 / local disk) drops the bytes via Frappe's standard
	on_trash hook.
	"""
	cutoff = add_to_date(now_datetime(), days=-RETENTION_DAYS)

	old_files = frappe.get_all(
		"File",
		filters={
			"file_name": ("like", "call_recording_%"),
			"attached_to_doctype": ("in", ("Call Log", "Twilio Call Log")),
			"creation": ("<", cutoff),
		},
		pluck="name",
	)

	if not old_files:
		return

	deleted = 0
	for name in old_files:
		try:
			frappe.delete_doc("File", name, ignore_permissions=True, force=True, delete_permanently=True)
			deleted += 1
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: failed to purge recording File {name}",
			)

	frappe.db.commit()
	frappe.logger().info(
		f"Voice Ops: purged {deleted}/{len(old_files)} recording files older than {RETENTION_DAYS} days"
	)
