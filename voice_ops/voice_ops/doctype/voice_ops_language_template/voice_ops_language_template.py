"""
Voice Ops Language Template DocType controller.

Child table row that lives on `Voice Ops Settings.language_template_map`.
Maps a Sarvam-detected BCP-47 language code (e.g. `hi-IN`) to the
Twilio/Exotel WhatsApp Template Reference that should be used for
driver acknowledgements in that language. Populated by the
`register_driver_ack_whatsapp_templates` patch on install; consumed
by `services/ack_sender.py`. No server-side behaviour.
"""

from frappe.model.document import Document


class VoiceOpsLanguageTemplate(Document):
	pass
