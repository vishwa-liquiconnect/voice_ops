"""
Call Log Dynamic Links

Thin helper around the core `Call Log.links` child table (Dynamic Link
rows). Populated at transcription + issue-creation time so downstream
consumers (digest, reporting, manual review) can read context off the
Call Log itself instead of re-resolving the caller each time.
"""

import frappe


def add_call_log_links(call_log_name, links):
	"""Append one or more Dynamic Link rows to `Call Log.links`, idempotent.

	`links` is an iterable of `(link_doctype, link_name)` tuples. Rows
	that are missing, point to nonexistent targets, or already exist on
	this Call Log are silently skipped. The Call Log is saved once at
	the end — only if at least one new row was added.
	"""
	if not call_log_name or not links:
		return
	if not frappe.db.exists("Call Log", call_log_name):
		return

	try:
		doc = frappe.get_doc("Call Log", call_log_name)
	except Exception:
		return

	existing = {(row.link_doctype, row.link_name) for row in (doc.links or [])}
	added = False

	for link_doctype, link_name in links:
		if not link_doctype or not link_name:
			continue
		key = (link_doctype, link_name)
		if key in existing:
			continue
		if not frappe.db.exists(link_doctype, link_name):
			continue
		doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})
		existing.add(key)
		added = True

	if not added:
		return

	try:
		doc.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: failed to update links on Call Log {call_log_name}",
		)
