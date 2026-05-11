"""
Voice Ops Settings DocType controller.

Single (`issingle: 1`) doctype that holds every tenant-level switch for
the app: provider credentials (Twilio / Exotel / Sarvam / Claude),
feature toggles (`enabled`, `enable_voicemail_digest`,
`enable_driver_ack`, …), provider selection per flow
(`outbound_telephony_provider`, `inbound_telephony_provider`,
`feedback_telephony_provider`), default driver language, synonym table,
and the WhatsApp language→template map.

This module exposes two small helpers used across the codebase so callers
don't keep repeating `frappe.get_single("Voice Ops Settings")`.
"""

import frappe
from frappe.model.document import Document


class VoiceOpsSettings(Document):
	pass


def get_settings():
	"""Return the Voice Ops Settings singleton."""
	return frappe.get_single("Voice Ops Settings")


def is_enabled():
	"""Whether the master `enabled` switch on Voice Ops Settings is on.

	Every scheduler entry point and most webhooks call this first and
	no-ops silently when the app is disabled — important on benches
	where voice_ops is installed but not configured (Turno-incident
	lesson: providers must not act on benches they weren't configured
	for).
	"""
	return bool(frappe.db.get_single_value("Voice Ops Settings", "enabled"))
