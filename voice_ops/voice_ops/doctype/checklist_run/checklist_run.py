import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ChecklistRun(Document):

	def validate(self):
		if not self.mobile_number and self.crew_member:
			self.mobile_number = frappe.db.get_value(
				"Trip Crew Member", self.crew_member, "mobile_number"
			)

	def after_insert(self):
		if self.checklist_template and not self.responses:
			self.populate_responses_from_template()
			self.save(ignore_permissions=True)

	def before_save(self):
		if self.has_value_changed("review_action") and self.review_action:
			self.reviewed_by = frappe.session.user
			self.reviewed_at = now_datetime()
			if self.review_action == "Approved":
				self.status = "Approved"
			elif self.review_action == "Rejected":
				self.status = "Rejected"
			elif self.review_action == "Reopened":
				self.status = "Needs Review"

	def populate_responses_from_template(self):
		"""Pre-fill response rows from the linked Checklist Template."""
		if not self.checklist_template:
			return

		template = frappe.get_doc("Checklist Template", self.checklist_template)
		self.responses = []
		for q in sorted(template.questions, key=lambda x: x.sequence or 0):
			self.append("responses", {
				"question_key": q.question_key,
				"question_text": q.question_text,
				"response_type": q.expected_response_type,
				"is_blocker": q.is_blocker,
				"is_warning": q.is_warning,
			})
