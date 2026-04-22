"""
Idempotent seed of the driver-ack WhatsApp templates.

Registers 10 WhatsApp Template Reference docs (one per supported Indic
language) with their Twilio Content SIDs, then fills the
language_template_map child table on Voice Ops Settings so the ack
sender picks the right template based on Sarvam's detected language.

Safe to re-run: existing rows are left alone; content_sid is refreshed
in place only when it differs. Silently skips everything when the
twilio_integration app (which owns "WhatsApp Template Reference") is
not installed on this site.
"""

import frappe


# (template_name, content_sid, detected_language_bcp47, twilio_language_code)
TEMPLATES = [
	("driver_ack_en", "HX412d3e797dac790eb226e93a8acddf3d", "en-IN", "en"),
	("driver_ack_hi", "HX3cdb1ea6ab00f25e11772e2593138671", "hi-IN", "hi"),
	("driver_ack_ta", "HXcc9ac81cd3c5d1f7a4b228ed969b3c06", "ta-IN", "ta"),
	("driver_ack_te", "HX5a700673246625174acacc0c1edf91e7", "te-IN", "te"),
	("driver_ack_kn", "HXc99885d671cfded0a0ab61e8a7bf22c9", "kn-IN", "kn"),
	("driver_ack_mr", "HX2b0f1282c90fae3d510845d0ce518136", "mr-IN", "mr"),
	("driver_ack_bn", "HX925a871a4859ce69b8af703c9df07984", "bn-IN", "bn"),
	("driver_ack_gu", "HX71b2fd46e6e786aca88ddb685638f97a", "gu-IN", "gu"),
	("driver_ack_ml", "HX261f57259e14c8509dc01fb448c5f3b4", "ml-IN", "ml"),
	("driver_ack_pa", "HX6f8f725b3582803a4aa6533fb140426c", "pa-IN", "pa"),
]


def execute():
	if not frappe.db.exists("DocType", "WhatsApp Template Reference"):
		# twilio_integration app not installed on this site; nothing to seed.
		return

	_ensure_template_refs()
	_ensure_language_map()
	frappe.db.commit()


def _ensure_template_refs():
	for name, sid, _bcp47, _twlang in TEMPLATES:
		if frappe.db.exists("WhatsApp Template Reference", name):
			existing = frappe.db.get_value(
				"WhatsApp Template Reference", name, "content_sid"
			) or ""
			if existing.strip() != sid:
				frappe.db.set_value(
					"WhatsApp Template Reference", name, "content_sid", sid
				)
			continue

		doc = frappe.get_doc({
			"doctype": "WhatsApp Template Reference",
			"template_name": name,
			"content_sid": sid,
		})
		doc.insert(ignore_permissions=True)


def _ensure_language_map():
	if not frappe.db.exists("DocType", "Voice Ops Language Template"):
		return

	settings = frappe.get_single("Voice Ops Settings")
	existing = {
		(row.language_code or "").strip(): row
		for row in (settings.get("language_template_map") or [])
	}
	changed = False

	for name, _sid, bcp47, twlang in TEMPLATES:
		row = existing.get(bcp47)
		if row is None:
			settings.append("language_template_map", {
				"language_code": bcp47,
				"whatsapp_template": name,
				"template_language_code": twlang,
			})
			changed = True
			continue

		# Refresh stale values on an existing row without duplicating it.
		if (row.whatsapp_template or "").strip() != name:
			row.whatsapp_template = name
			changed = True
		if (row.template_language_code or "").strip() != twlang:
			row.template_language_code = twlang
			changed = True

	if changed:
		settings.save(ignore_permissions=True)
