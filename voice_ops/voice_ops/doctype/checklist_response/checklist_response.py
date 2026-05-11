"""
Checklist Response DocType controller.

Child table row that lives on `Checklist Run.responses`. One row per
question; gets created up-front by `ChecklistRun.populate_responses_from_template`
and then enriched during the IVR (`recording_url`) and post-call
processing (`raw_transcript`, `normalized_response`, `is_blocker_triggered`,
`is_warning_triggered`, `confidence`). No server-side behaviour beyond
the framework default — all enrichment is driven externally by the
jobs/ and services/ packages.
"""

from frappe.model.document import Document


class ChecklistResponse(Document):
	pass
