"""
Patch: add the `custom_detected_language` custom field to Call Log.

Populated by Sarvam's `language_code` at voicemail-transcription time
and consumed by `services.language.resolve_driver_language` to pick the
WhatsApp ack language. Read-only on the form; `create_custom_fields`
is idempotent, so re-running is safe.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add `custom_detected_language` Data field on Call Log.

	Populated by Sarvam's `language_code` at voicemail-transcription
	time; consumed by `voice_ops.services.language.resolve_driver_language`
	to pick the WhatsApp ack language."""
	create_custom_fields(
		{
			"Call Log": [
				{
					"fieldname": "custom_detected_language",
					"label": "Detected Language",
					"fieldtype": "Data",
					"insert_after": "summary",
					"description": "BCP-47 code (e.g. hi-IN, ta-IN) detected by Sarvam at transcription time.",
					"read_only": 1,
				}
			]
		},
		ignore_validate=True,
	)
