"""
Voicemail Digest Entry DocType controller.

Child table row that lives on `Voicemail Digest Log.voicemails`. One row
per voicemail Call Log included in a digest window. Populated by
`VoicemailDigestLog.generate()`; the doctype itself has no behaviour.
"""

from frappe.model.document import Document


class VoicemailDigestEntry(Document):
	pass
