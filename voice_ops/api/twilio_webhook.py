"""
Twilio Webhook Handlers for Voice Ops

Per-question IVR flow:
1. twiml_response - Called when call connects. Plays the language selection menu.
2. language_callback - Maps DTMF digit to language, persists it, asks first question.
3. recording_callback - Called after each answer. Stores recording, asks next question.
   When all questions are done, says thank you and enqueues processing.

All endpoints are allow_guest=True since Twilio calls them without auth.
All doc reads use frappe.get_cached_doc or flags.ignore_permissions.
"""

import frappe
from werkzeug.wrappers import Response

from voice_ops.services.language import localized_question_text
from voice_ops.services.telephony import (
	build_callback_url,
	build_error_xml,
	build_gather_xml,
	build_goodbye_xml,
	build_greeting_record_xml,
	build_say_record_xml,
)


# Language options mapped to DTMF digits (mirrors inbound flow)
_LANG_DIGITS = {
	"1": "en-IN",
	"2": "hi-IN",
	"3": "ta-IN",
	"4": "te-IN",
	"5": "kn-IN",
}

# System phrases per language (greeting fallback, no-input, goodbye fallback).
# Question text comes from the template + on-the-fly translation; these are
# the fixed shells around the questions.
_PROMPTS = {
	"en-IN": {
		"intro": "Hello. Your checklist is starting.",
		"no_input": "No response received.",
		"no_input_next": "No response. Next question.",
		"goodbye": "Thank you. Your checklist is complete.",
	},
	"hi-IN": {
		"intro": "Namaste. Aapki checklist shuru hoti hai.",
		"no_input": "Koi jawab nahi mila.",
		"no_input_next": "Koi jawab nahi mila. Agla sawaal.",
		"goodbye": "Dhanyavaad. Aapka checklist poora ho gaya hai.",
	},
	"ta-IN": {
		"intro": "Vanakkam. Ungal checklist thodangukirathu.",
		"no_input": "Badhil varavillai.",
		"no_input_next": "Badhil varavillai. Adutha kelvi.",
		"goodbye": "Nandri. Ungal checklist mudivu adaindhullathu.",
	},
	"te-IN": {
		"intro": "Namaskaaram. Mee checklist modaludutondi.",
		"no_input": "Samadhanam raledu.",
		"no_input_next": "Samadhanam raledu. Tarvati prashna.",
		"goodbye": "Dhanyavaadaalu. Mee checklist poorthayindi.",
	},
	"kn-IN": {
		"intro": "Namaskara. Nimma checklist aarambhavaagide.",
		"no_input": "Uttara barilla.",
		"no_input_next": "Uttara barilla. Mundina prashne.",
		"goodbye": "Dhanyavaadagalu. Nimma checklist poorna aagide.",
	},
}


def _get_template_and_questions(checklist_run):
	"""Get the checklist template and its sorted questions."""
	template = frappe.get_doc("Checklist Template", checklist_run.checklist_template)
	questions = sorted(template.questions, key=lambda x: x.sequence or 0)
	return template, questions


def _get_question_text(question, language):
	"""Get question text in the selected language, translating on demand."""
	return localized_question_text(question, language)


def _get_language(checklist_run):
	"""Resolve the language to use for this run.

	Prefers the language the driver picked on the IVR menu. Falls back to
	the template default, then Hindi.
	"""
	picked = (getattr(checklist_run, "selected_language", None) or "").strip()
	if picked:
		return picked
	return frappe.db.get_value(
		"Checklist Template", checklist_run.checklist_template, "language"
	) or "hi-IN"


def _get_prompts(language):
	return _PROMPTS.get(language, _PROMPTS["hi-IN"])


def _get_call_settings():
	"""Get max_recording_length and default_response_timeout from Voice Ops Settings."""
	return {
		"max_recording_length": frappe.db.get_single_value("Voice Ops Settings", "max_recording_length") or 30,
		"default_response_timeout": frappe.db.get_single_value("Voice Ops Settings", "default_response_timeout") or 5,
	}


@frappe.whitelist(allow_guest=True)
def twiml_response():
	"""
	Called when Twilio connects the call.
	Plays the language selection menu; the driver's digit choice is handled
	by `language_callback`.
	"""
	try:
		checklist_run_name = frappe.form_dict.get("checklist_run")

		if not checklist_run_name or not frappe.db.exists("Checklist Run", checklist_run_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response>'
				'<Say language="hi-IN">Namaste. Recording shuru ho rahi hai.</Say>'
				'<Record maxLength="300" playBeep="true" timeout="5" />'
				'<Say language="hi-IN">Dhanyavaad.</Say></Response>',
				mimetype="text/xml",
			)

		frappe.flags.ignore_permissions = True

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		_template, questions = _get_template_and_questions(checklist_run)

		if not questions:
			return Response(build_goodbye_xml("hi-IN", "Dhanyavaad."), mimetype="text/xml")

		action_url = build_callback_url(
			"voice_ops.api.twilio_webhook.language_callback",
			checklist_run=checklist_run_name,
		)

		prompt_lines = [
			("en-IN", "Welcome. Please select your language."),
			("hi-IN", "Apni bhasha chunein."),
			("en-IN", "Press 1 for English."),
			("hi-IN", "Hindi ke liye 2 dabaiye."),
			("ta-IN", "Tamil-kku 3 azhuthavum."),
			("te-IN", "Telugu kosam 4 noppandi."),
			("kn-IN", "Kannada ge 5 odiri."),
		]

		twiml = build_gather_xml(
			prompt_lines=prompt_lines,
			action_url=action_url,
			num_digits=1,
			timeout=10,
		)

		return Response(twiml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: TwiML Response Failed")
		return Response(build_error_xml(), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def language_callback():
	"""
	Called after the driver presses a digit on the language menu.
	Persists the selected language on the Checklist Run, then plays the
	greeting and the first question in that language.

	Pre-translates remaining questions in the background so subsequent
	turns don't pay the Claude latency.
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		checklist_run_name = args.get("checklist_run")
		digits = (form.get("Digits") or "").strip()

		if not checklist_run_name or not frappe.db.exists("Checklist Run", checklist_run_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
				mimetype="text/xml",
			)

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		template, questions = _get_template_and_questions(checklist_run)

		if not questions:
			return Response(build_goodbye_xml("hi-IN", "Dhanyavaad."), mimetype="text/xml")

		# Map digit → language. If no digit / invalid, fall back to the
		# template default so the call still proceeds.
		language = _LANG_DIGITS.get(digits)
		if not language:
			language = frappe.db.get_value(
				"Checklist Template", checklist_run.checklist_template, "language"
			) or "hi-IN"

		frappe.db.set_value("Checklist Run", checklist_run_name, "selected_language", language)
		frappe.db.commit()

		# Warm the translation cache for the rest of the questions while
		# the driver hears Q1. First-question translation is synchronous.
		if len(questions) > 1:
			frappe.enqueue(
				"voice_ops.jobs.pretranslate_questions.run",
				queue="short",
				template_name=checklist_run.checklist_template,
				language=language,
			)

		prompts = _get_prompts(language)
		call_settings = _get_call_settings()
		first_question = _get_question_text(questions[0], language)

		callback_url = build_callback_url(
			"voice_ops.api.twilio_webhook.recording_callback",
			checklist_run=checklist_run_name, question_idx=0,
		)
		status_callback_url = build_callback_url(
			"voice_ops.api.twilio_webhook.recording_status",
			checklist_run=checklist_run_name, question_idx=0,
		)

		greeting = template.intro_text or prompts["intro"]
		timeout = questions[0].response_timeout or call_settings["default_response_timeout"]
		max_length = call_settings["max_recording_length"]

		twiml = build_greeting_record_xml(
			language=language,
			greeting_text=greeting,
			question_text=first_question,
			record_callback_url=callback_url,
			status_callback_url=status_callback_url,
			timeout=timeout,
			max_length=max_length,
			no_input_text=prompts["no_input"],
			redirect_url=callback_url,
		)

		return Response(twiml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Language Callback Failed")
		return Response(build_error_xml(), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def recording_callback():
	"""
	Called after each question's recording ends.
	Stores the recording URL on the response row, then serves the next question.
	When all questions are done, enqueues processing.
	"""
	try:
		frappe.flags.ignore_permissions = True

		# Query params from URL, POST body from Twilio's form-encoded data
		args = frappe.request.args
		form = frappe.request.form

		checklist_run_name = args.get("checklist_run")
		question_idx = int(args.get("question_idx", 0))
		# Twilio sends RecordingUrl in POST body (action callback)
		recording_url = form.get("RecordingUrl")
		call_sid = form.get("CallSid")

		if not checklist_run_name or not frappe.db.exists("Checklist Run", checklist_run_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
				mimetype="text/xml",
			)

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		template, questions = _get_template_and_questions(checklist_run)
		language = _get_language(checklist_run)
		prompts = _get_prompts(language)

		# Store recording URL on the current response row
		if recording_url and question_idx < len(checklist_run.responses):
			if not recording_url.endswith((".mp3", ".wav")):
				recording_url = f"{recording_url}.mp3"

			response_row = checklist_run.responses[question_idx]
			response_row.recording_url = recording_url
			checklist_run.save(ignore_permissions=True)
			frappe.db.commit()

		# Determine next question
		next_idx = question_idx + 1

		if next_idx < len(questions):
			call_settings = _get_call_settings()
			next_question = _get_question_text(questions[next_idx], language)
			next_callback_url = build_callback_url(
				"voice_ops.api.twilio_webhook.recording_callback",
				checklist_run=checklist_run_name, question_idx=next_idx,
			)
			next_status_url = build_callback_url(
				"voice_ops.api.twilio_webhook.recording_status",
				checklist_run=checklist_run_name, question_idx=next_idx,
			)
			timeout = questions[next_idx].response_timeout or call_settings["default_response_timeout"]
			max_length = call_settings["max_recording_length"]
			twiml = build_say_record_xml(
				language=language,
				say_text=next_question,
				record_callback_url=next_callback_url,
				status_callback_url=next_status_url,
				timeout=timeout,
				max_length=max_length,
				no_input_text=prompts["no_input_next"],
				redirect_url=next_callback_url,
			)
			return Response(twiml, mimetype="text/xml")

		# All questions done — trigger processing
		frappe.db.set_value("Checklist Run", checklist_run_name, {
			"status": "Call Completed",
			"completed_at": frappe.utils.now_datetime(),
		})

		twilio_log_name = None
		if call_sid:
			twilio_log_name = frappe.db.get_value(
				"Twilio Call Log", {"call_sid": call_sid, "type": "Call"}, "name"
			)

		frappe.enqueue(
			"voice_ops.jobs.process_call_recording.process",
			queue="long",
			twilio_log_name=twilio_log_name,
			checklist_run_name=checklist_run_name,
		)
		frappe.db.commit()

		return Response(
			build_goodbye_xml(language, template.outro_text or prompts["goodbye"]),
			mimetype="text/xml",
		)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Recording Callback Failed")
		return Response(build_error_xml(), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def recording_status():
	"""
	Reliable fallback: called by Twilio when the recording file is ready.

	The action callback may fire before the recording is accessible.
	This endpoint fires later when the file is truly available,
	and stores the recording URL if not already stored.
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		checklist_run_name = args.get("checklist_run")
		question_idx = int(args.get("question_idx", 0))
		recording_url = form.get("RecordingUrl")
		rec_status = form.get("RecordingStatus")

		if not checklist_run_name or not recording_url:
			return

		if rec_status and rec_status != "completed":
			return

		if not recording_url.endswith((".mp3", ".wav")):
			recording_url = f"{recording_url}.mp3"

		if not frappe.db.exists("Checklist Run", checklist_run_name):
			return

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		if question_idx < len(checklist_run.responses):
			response_row = checklist_run.responses[question_idx]
			# Only update if not already stored (idempotent)
			if not response_row.recording_url:
				response_row.recording_url = recording_url
				checklist_run.save(ignore_permissions=True)
				frappe.db.commit()

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Recording Status Failed")
	finally:
		frappe.flags.ignore_permissions = False
