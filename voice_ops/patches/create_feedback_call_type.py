"""
Patch: ensure the "Feedback" Telephony Call Type exists.

Same idempotent wrapper as create_voicemail_call_type — calling
`ensure_telephony_call_types` creates both "Voicemail" and "Feedback"
rows if missing. Listed separately so existing benches that already
ran the voicemail patch still pick the feedback type up on migrate.
"""

import frappe  # noqa: F401 — kept so patches.txt can resolve the dotted import path


def execute():
	from voice_ops.setup import ensure_telephony_call_types

	ensure_telephony_call_types()
