"""
Backfill `Checklist Run.call_log_doctype` for historical rows.

Before the Dynamic Link refactor, `call_log` pointed only at Twilio
Call Log. Old rows therefore hold a Twilio Call Log name and a NULL
doctype — which breaks Dynamic Link resolution on save. Populate the
companion field so existing data stays valid.

Exotel wasn't in use on outbound until this change, so every
non-null call_log is a Twilio Call Log.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabChecklist Run`
		SET call_log_doctype = 'Twilio Call Log'
		WHERE call_log IS NOT NULL
		  AND call_log != ''
		  AND (call_log_doctype IS NULL OR call_log_doctype = '')
		"""
	)
	frappe.db.commit()
