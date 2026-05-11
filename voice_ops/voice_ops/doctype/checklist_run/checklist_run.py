"""
Checklist Run DocType controller.

A `Checklist Run` represents one *attempt* to collect a checklist from a
specific driver — most often a pre-departure or post-trip call. It owns:

- the driver's mobile number and crew member link
- the chosen Checklist Template (the question set)
- a child table of Checklist Response rows (one per question)
- the call status (Draft → Call Initiated → In Progress → Completed /
  Failed / Approved / Rejected / Needs Review)
- the linked Call Log (Twilio or Exotel — Dynamic Link via
  ``call_log_doctype`` + ``call_log``)

The lifecycle below is what this controller enforces; the actual call
plumbing, transcription, and rule evaluation live in the api/, jobs/
and services/ packages.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ChecklistRun(Document):

	def validate(self):
		# Convenience auto-fill: if the operator picked a crew member but
		# didn't type a phone number, copy it from Trip Crew Member so the
		# call can actually be placed.
		if not self.mobile_number and self.crew_member:
			self.mobile_number = frappe.db.get_value(
				"Trip Crew Member", self.crew_member, "mobile_number"
			)

	def after_insert(self):
		# A fresh run with a template but no responses yet is the common
		# case (manual create or auto_trigger). Materialize one response
		# row per template question so the IVR has something to walk
		# through and reviewers see the full questionnaire even before
		# answers come in.
		if self.checklist_template and not self.responses:
			self.populate_responses_from_template()
			self.save(ignore_permissions=True)

	def before_save(self):
		# When a reviewer toggles `review_action`, stamp who/when and map
		# the action to a terminal status. Manual reviews are how we
		# resolve low-confidence transcripts (CLAUDE.md: "All manual
		# actions must be audited").
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
		"""Pre-fill response rows from the linked Checklist Template.

		Copies each template question (in `sequence` order) into a fresh
		Checklist Response row, carrying over the response type and the
		blocker/warning flags so the rule evaluator has the metadata it
		needs without re-reading the template later.
		"""
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
