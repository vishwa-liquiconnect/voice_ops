"""
Pre-translate Checklist Questions

Fired from `language_callback` immediately after the driver picks a language.
While the driver hears the greeting + Q1, this job translates Q2..Qn in the
background and stores them in the cache used by
`voice_ops.services.language.localized_question_text`. Subsequent IVR turns
hit the cache and don't pay Claude latency live on the call.

Best-effort: failures are logged but never block the call.
"""

import frappe

from voice_ops.services.language import localized_question_text


def run(template_name, language):
	"""Translate every question on `template_name` into `language`.

	Idempotent and cache-aware: questions that already have a translation
	cached, an English target, or a hand-written `question_text_hi` fall
	through cheaply.
	"""
	if not template_name or not language:
		return

	try:
		template = frappe.get_doc("Checklist Template", template_name)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Pre-translate could not load template {template_name}",
		)
		return

	for question in template.questions or []:
		try:
			localized_question_text(question, language)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Pre-translate failed for "
				f"{template_name}/{getattr(question, 'question_key', '?')} ({language})",
			)
