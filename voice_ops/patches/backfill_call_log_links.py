"""
Backfill `Call Log.links` for historical voicemails.

Mirrors the transcription + issue-creation hooks that run for new
voicemails going forward:

- resolve caller from `from` and attach Contact / Employee / Vehicle
- find any Issue whose description back-links to this Call Log and
  attach the Issue

Uses the shared `add_call_log_links` helper, so it is idempotent —
safe to re-run, safe to leave in patches.txt.
"""

import frappe


def execute():
	from voice_ops.services.caller_lookup import resolve_caller
	from voice_ops.services.call_log_links import add_call_log_links

	rows = frappe.db.sql(
		"""
		SELECT name, `from`
		FROM `tabCall Log`
		WHERE type_of_call = 'Voicemail'
		ORDER BY creation ASC
		""",
		as_dict=True,
	)

	processed = 0
	for row in rows:
		try:
			info = resolve_caller(row.get("from")) or {}
			links = [
				("Contact", info.get("contact")),
				("Employee", info.get("employee")),
				("Vehicle", info.get("vehicle")),
			]

			issue_rows = frappe.db.sql(
				"""
				SELECT name FROM `tabIssue`
				WHERE description LIKE %s
				""",
				(f"%/app/call-log/{row.name}%",),
				as_dict=True,
			)
			for issue in issue_rows:
				links.append(("Issue", issue.name))

			add_call_log_links(row.name, links)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: backfill links failed for Call Log {row.name}",
			)
			continue

		processed += 1
		if processed % 100 == 0:
			frappe.db.commit()

	frappe.db.commit()
