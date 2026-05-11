"""
Voice Ops Synonym DocType controller.

Child table row that lives on `Voice Ops Settings.synonyms`. Lets ops
add language-specific yes/no/maybe equivalents on top of the built-in
defaults baked into `services/transcript_processor.py`. Useful for
regional pronunciations the defaults don't cover (e.g. "kaa" vs "haan"
for yes in some dialects). No server-side behaviour.
"""

from frappe.model.document import Document


class VoiceOpsSynonym(Document):
	pass
