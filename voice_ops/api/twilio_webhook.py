"""
Twilio Webhook Handlers for Voice Ops

Per-question IVR flow:
1. twiml_response - Called when call connects. Greets driver, asks first question.
2. recording_callback - Called after each answer. Stores recording, asks next question.
   When all questions are done, says thank you and enqueues processing.

All endpoints are allow_guest=True since Twilio calls them without auth.
All doc reads use frappe.get_cached_doc or flags.ignore_permissions.
"""

import frappe
from frappe.utils import get_url
from werkzeug.wrappers import Response

# Error TwiML to avoid Twilio "application error" message
_ERROR_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="hi-IN">Kuch galat ho gaya. Kripya baad mein try karein.</Say><Hangup/></Response>'


def _force_https(url):
	from twilio_integration.twilio_integration.doctype.twilio_call_log.twilio_call_log import force_https
	return force_https(url)


def _get_template_and_questions(checklist_run):
	"""Get the checklist template and its sorted questions."""
	template = frappe.get_doc("Checklist Template", checklist_run.checklist_template)
	questions = sorted(template.questions, key=lambda x: x.sequence or 0)
	return template, questions


def _get_question_text(question, language):
	"""Get question text in the appropriate language."""
	if language == "hi-IN" and question.question_text_hi:
		return question.question_text_hi
	return question.question_text


def _get_language(checklist_run):
	"""Get language from the checklist template."""
	return frappe.db.get_value(
		"Checklist Template", checklist_run.checklist_template, "language"
	) or "hi-IN"


def _build_question_twiml(question_text, language, callback_url, status_callback_url, timeout=10):
	"""Build TwiML for asking a question and recording the answer."""
	max_length = timeout * 6
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
	<Say language="{language}">{question_text}</Say>
	<Record action="{callback_url}" recordingStatusCallback="{status_callback_url}" recordingStatusCallbackMethod="POST" timeout="{timeout}" maxLength="{max_length}" playBeep="false" />
	<Say language="{language}">Koi jawab nahi mila. Agla sawaal.</Say>
	<Redirect>{callback_url}</Redirect>
</Response>"""


def _build_goodbye_twiml(language, outro_text=None):
	"""Build TwiML for the end of the checklist."""
	goodbye = outro_text or "Dhanyavaad. Aapka checklist poora ho gaya hai."
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
	<Say language="{language}">{goodbye}</Say>
	<Hangup/>
</Response>"""


def _build_callback_url(checklist_run_name, question_idx):
	"""Build the HTTPS callback URL for a given question index, XML-escaped."""
	site_url = get_url()
	raw_url = _force_https(
		f"{site_url}/api/method/voice_ops.api.twilio_webhook.recording_callback"
		f"?checklist_run={checklist_run_name}&question_idx={question_idx}"
	)
	return raw_url.replace("&", "&amp;")


def _build_status_callback_url(checklist_run_name, question_idx):
	"""Build the HTTPS recording status callback URL, XML-escaped."""
	site_url = get_url()
	raw_url = _force_https(
		f"{site_url}/api/method/voice_ops.api.twilio_webhook.recording_status"
		f"?checklist_run={checklist_run_name}&question_idx={question_idx}"
	)
	return raw_url.replace("&", "&amp;")


@frappe.whitelist(allow_guest=True)
def twiml_response():
	"""
	Called when Twilio connects the call.
	Greets the driver and asks the first checklist question.
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
		template, questions = _get_template_and_questions(checklist_run)

		if not questions:
			return Response(_build_goodbye_twiml("hi-IN"), mimetype="text/xml")

		language = _get_language(checklist_run)
		first_question = _get_question_text(questions[0], language)
		callback_url = _build_callback_url(checklist_run_name, 0)
		status_callback_url = _build_status_callback_url(checklist_run_name, 0)

		greeting = template.intro_text or (
			"Namaste. Aapki checklist shuru hoti hai." if language == "hi-IN"
			else "Hello. Your checklist is starting."
		)
		timeout = questions[0].response_timeout or 10
		max_length = timeout * 6

		twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
	<Say language="{language}">{greeting}</Say>
	<Pause length="1"/>
	<Say language="{language}">{first_question}</Say>
	<Record action="{callback_url}" recordingStatusCallback="{status_callback_url}" recordingStatusCallbackMethod="POST" timeout="{timeout}" maxLength="{max_length}" playBeep="false" />
	<Say language="{language}">Koi jawab nahi mila.</Say>
	<Redirect>{callback_url}</Redirect>
</Response>"""

		return Response(twiml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: TwiML Response Failed")
		return Response(_ERROR_TWIML, mimetype="text/xml")
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
			next_question = _get_question_text(questions[next_idx], language)
			next_callback_url = _build_callback_url(checklist_run_name, next_idx)
			next_status_url = _build_status_callback_url(checklist_run_name, next_idx)
			timeout = questions[next_idx].response_timeout or 10
			twiml = _build_question_twiml(next_question, language, next_callback_url, next_status_url, timeout)
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

		return Response(_build_goodbye_twiml(language, template.outro_text), mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Recording Callback Failed")
		return Response(_ERROR_TWIML, mimetype="text/xml")
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
		recording_status = form.get("RecordingStatus")

		if not checklist_run_name or not recording_url:
			return

		if recording_status and recording_status != "completed":
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
