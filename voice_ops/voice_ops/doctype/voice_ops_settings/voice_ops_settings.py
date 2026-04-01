import frappe
from frappe.model.document import Document


class VoiceOpsSettings(Document):
	pass


def get_settings():
	"""Return the Voice Ops Settings singleton."""
	return frappe.get_single("Voice Ops Settings")


def is_enabled():
	"""Check if Voice Ops is enabled."""
	return bool(frappe.db.get_single_value("Voice Ops Settings", "enabled"))
