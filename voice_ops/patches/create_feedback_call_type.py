import frappe


def execute():
	from voice_ops.setup import ensure_telephony_call_types

	ensure_telephony_call_types()
