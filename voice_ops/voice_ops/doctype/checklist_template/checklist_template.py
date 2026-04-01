import frappe
from frappe.model.document import Document


class ChecklistTemplate(Document):

	def validate(self):
		self.validate_unique_question_keys()
		self.set_question_sequence()

	def validate_unique_question_keys(self):
		keys = [q.question_key for q in self.questions]
		duplicates = {k for k in keys if keys.count(k) > 1}
		if duplicates:
			frappe.throw(f"Duplicate question keys found: {', '.join(duplicates)}")

	def set_question_sequence(self):
		for idx, question in enumerate(self.questions):
			if not question.sequence:
				question.sequence = (idx + 1) * 10
