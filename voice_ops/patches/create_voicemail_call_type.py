"""
Patch: ensure the "Voicemail" Telephony Call Type exists.

Idempotent thin wrapper around `voice_ops.setup.ensure_telephony_call_types`,
which creates both "Voicemail" and "Feedback" rows if missing. Listed as
its own patch so existing benches installed before this type was
introduced pick it up on next migrate.
"""

import frappe  # noqa: F401 — kept so patches.txt can resolve the dotted import path


def execute():
	from voice_ops.setup import ensure_telephony_call_types

	ensure_telephony_call_types()
