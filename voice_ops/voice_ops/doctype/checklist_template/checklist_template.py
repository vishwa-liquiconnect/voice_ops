"""
Checklist Template DocType controller.

A `Checklist Template` is the question set used by checklist calls
(e.g. "Pre-Boarding Checklist", "Post-Trip Checklist"). It is a parent
doc with a `questions` child table of Checklist Question rows. Each
question carries a stable `question_key` — that key is what the rule
evaluator and transcript processor use to look up business rules
(see services/rule_evaluator.py), so keys must be unique inside one
template.

This controller only does light housekeeping on save; the template is
otherwise plain configuration data managed by ops.
"""

import frappe
from frappe.model.document import Document


class ChecklistTemplate(Document):

	def validate(self):
		self.validate_unique_question_keys()
		self.set_question_sequence()

	def validate_unique_question_keys(self):
		"""Reject duplicate `question_key`s inside one template.

		Rules in services/rule_evaluator.py are keyed by question_key,
		so two rows sharing a key would mean the same rule fires twice
		(or the wrong row gets the answer). Easier to fail loudly here.
		"""
		keys = [q.question_key for q in self.questions]
		duplicates = {k for k in keys if keys.count(k) > 1}
		if duplicates:
			frappe.throw(f"Duplicate question keys found: {', '.join(duplicates)}")

	def set_question_sequence(self):
		"""Auto-number any question that didn't get a sequence number.

		Sequence is multiplied by 10 (10, 20, 30, …) so ops can later
		insert a new question between two existing ones without
		renumbering the whole list. The IVR walks questions in
		sequence order.
		"""
		for idx, question in enumerate(self.questions):
			if not question.sequence:
				question.sequence = (idx + 1) * 10
