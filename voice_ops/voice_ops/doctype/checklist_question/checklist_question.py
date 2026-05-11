"""
Checklist Question DocType controller.

Child table row that lives on `Checklist Template.questions`. Holds the
prompt text, expected response type (Yes/No / numeric / free text),
sequence number, and the blocker/warning flags consumed by the rule
evaluator. No server-side behaviour beyond the framework default —
validation is done on the parent template.
"""

from frappe.model.document import Document


class ChecklistQuestion(Document):
	pass
