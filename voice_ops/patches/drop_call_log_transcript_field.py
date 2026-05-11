"""
Patch: drop the legacy `transcript` custom field from Call Log.

Raw transcripts are now written to the core `summary` field at capture
time and rewritten with the Claude summary at digest time; the custom
field was redundant. Safe to re-run via `ignore_missing=True`.
"""

import frappe


def execute():
	"""Drop the legacy `transcript` custom field from Call Log.

	Raw transcripts are now written into the core `summary` field at
	capture time, then replaced with a Claude summary when the voicemail
	digest runs.
	"""
	frappe.delete_doc("Custom Field", "Call Log-transcript", ignore_missing=True, force=True)
