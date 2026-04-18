"""
Migrate old WhatsApp-named ack toggles to the new channel-agnostic
fieldnames on Voice Ops Settings.

- enable_route_manager_whatsapp  →  enable_route_manager_alert
- enable_driver_ack_whatsapp     →  enable_driver_ack

Copies whatever value was stored under the old fieldname into the new
one, then clears the old one. Silent no-op when the old field is
already gone on this bench.
"""

import frappe


MIGRATIONS = [
	("enable_route_manager_whatsapp", "enable_route_manager_alert"),
	("enable_driver_ack_whatsapp", "enable_driver_ack"),
]


def execute():
	for old_field, new_field in MIGRATIONS:
		try:
			old_value = frappe.db.get_single_value("Voice Ops Settings", old_field)
		except Exception:
			continue
		if old_value in (None, ""):
			continue

		frappe.db.set_single_value("Voice Ops Settings", new_field, old_value)
		# Clear the old value so stale data doesn't reappear if the field
		# is briefly re-introduced on another bench.
		try:
			frappe.db.set_single_value("Voice Ops Settings", old_field, None)
		except Exception:
			pass

	frappe.db.commit()
