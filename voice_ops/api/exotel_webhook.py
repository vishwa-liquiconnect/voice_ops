"""
Exotel Webhook Handlers for Voice Ops

ExoML-based IVR flow (mirrors twilio_webhook.py logic):
1. exoml_response - Called when outbound call connects. Greets driver, asks first question.
2. exotel_recording_callback - Called after each answer. Stores recording, asks next question.

Inbound driver query flow:
3. inbound_exoml - Called when driver calls in. Creates Driver Query, asks for name.
4. inbound_exotel_recording_callback - Steps through name → bus → query.

All endpoints are allow_guest=True since Exotel calls them as webhooks.
Exotel sends form-encoded POST data with keys like CallSid, RecordingUrl, etc.
"""

import frappe
from frappe.utils import now_datetime
from werkzeug.wrappers import Response

from voice_ops.services.telephony import (
	build_callback_url,
	build_error_xml,
	build_goodbye_xml,
	build_greeting_record_xml,
	build_say_record_xml,
)


# ---------------------------------------------------------------------------
# Outbound checklist flow (Exotel equivalent of twilio_webhook endpoints)
# ---------------------------------------------------------------------------

def _get_template_and_questions(checklist_run):
	template = frappe.get_doc("Checklist Template", checklist_run.checklist_template)
	questions = sorted(template.questions, key=lambda x: x.sequence or 0)
	return template, questions


def _get_question_text(question, language):
	if language == "hi-IN" and question.question_text_hi:
		return question.question_text_hi
	return question.question_text


def _get_language(checklist_run):
	return frappe.db.get_value(
		"Checklist Template", checklist_run.checklist_template, "language"
	) or "hi-IN"


def _get_call_settings():
	return {
		"max_recording_length": frappe.db.get_single_value("Voice Ops Settings", "max_recording_length") or 30,
		"default_response_timeout": frappe.db.get_single_value("Voice Ops Settings", "default_response_timeout") or 5,
	}


@frappe.whitelist(allow_guest=True)
def exoml_response():
	"""
	Called when Exotel connects an outbound call.
	Returns ExoML with greeting + first question + record.
	"""
	try:
		checklist_run_name = frappe.form_dict.get("checklist_run")

		if not checklist_run_name or not frappe.db.exists("Checklist Run", checklist_run_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response>'
				'<Say>Namaste. Recording shuru ho rahi hai.</Say>'
				'<Record maxLength="300" timeout="5" />'
				'<Say>Dhanyavaad.</Say></Response>',
				mimetype="text/xml",
			)

		frappe.flags.ignore_permissions = True

		checklist_run = frappe.get_doc("Checklist Run", checklist_run_name)
		template, questions = _get_template_and_questions(checklist_run)

		if not questions:
			return Response(build_goodbye_xml("hi-IN", "Dhanyavaad.", provider="Exotel"), mimetype="text/xml")

		language = _get_language(checklist_run)
		call_settings = _get_call_settings()
		first_question = _get_question_text(questions[0], language)
		callback_url = build_callback_url(
			"voice_ops.api.exotel_webhook.exotel_recording_callback",
			checklist_run=checklist_run_name, question_idx=0,
		)

		greeting = template.intro_text or (
			"Namaste. Aapki checklist shuru hoti hai." if language == "hi-IN"
			else "Hello. Your checklist is starting."
		)
		timeout = questions[0].response_timeout or call_settings["default_response_timeout"]
		max_length = call_settings["max_recording_length"]

		exoml = build_greeting_record_xml(
			language=language,
			greeting_text=greeting,
			question_text=first_question,
			record_callback_url=callback_url,
			timeout=timeout,
			max_length=max_length,
			no_input_text="Koi jawab nahi mila." if language == "hi-IN" else "No response received.",
			redirect_url=callback_url,
			provider="Exotel",
		)

		return Response(exoml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: ExoML Response Failed")
		return Response(build_error_xml(provider="Exotel"), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def exotel_recording_callback():
	"""
	Called after each question's recording ends (Exotel action callback).
	Stores recording URL, serves next question or enqueues processing.
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		checklist_run_name = args.get("checklist_run")
		question_idx = int(args.get("question_idx", 0))
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

		# Store recording URL
		if recording_url and question_idx < len(checklist_run.responses):
			response_row = checklist_run.responses[question_idx]
			response_row.recording_url = recording_url
			checklist_run.save(ignore_permissions=True)
			frappe.db.commit()

		next_idx = question_idx + 1

		if next_idx < len(questions):
			call_settings = _get_call_settings()
			next_question = _get_question_text(questions[next_idx], language)
			next_callback_url = build_callback_url(
				"voice_ops.api.exotel_webhook.exotel_recording_callback",
				checklist_run=checklist_run_name, question_idx=next_idx,
			)
			timeout = questions[next_idx].response_timeout or call_settings["default_response_timeout"]
			max_length = call_settings["max_recording_length"]
			exoml = build_say_record_xml(
				language=language,
				say_text=next_question,
				record_callback_url=next_callback_url,
				timeout=timeout,
				max_length=max_length,
				no_input_text="Koi jawab nahi mila. Agla sawaal." if language == "hi-IN" else "No response. Next question.",
				redirect_url=next_callback_url,
				provider="Exotel",
			)
			return Response(exoml, mimetype="text/xml")

		# All questions done
		frappe.db.set_value("Checklist Run", checklist_run_name, {
			"status": "Call Completed",
			"completed_at": now_datetime(),
		})

		call_log_name = None
		if call_sid:
			call_log_name = frappe.db.get_value("Call Log", call_sid, "name")

		frappe.enqueue(
			"voice_ops.jobs.process_call_recording.process",
			queue="long",
			twilio_log_name=call_log_name,
			checklist_run_name=checklist_run_name,
		)
		frappe.db.commit()

		return Response(
			build_goodbye_xml(language, template.outro_text or "Dhanyavaad. Aapka checklist poora ho gaya hai.", provider="Exotel"),
			mimetype="text/xml",
		)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Exotel Recording Callback Failed")
		return Response(build_error_xml(provider="Exotel"), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


# ---------------------------------------------------------------------------
# Inbound driver query flow (Exotel)
# ---------------------------------------------------------------------------

from voice_ops.services.telephony import build_gather_xml

# Language options mapped to DTMF digits
_LANG_DIGITS = {
	"1": "en-IN",
	"2": "hi-IN",
	"3": "ta-IN",
	"4": "te-IN",
	"5": "kn-IN",
}

# IVR prompts per language
_PROMPTS = {
	"hi-IN": {
		"intro": (
			"Dhanyavaad. Aap Liqui-Connect se jude hain. "
			"Kripya apna naam, gaadi ka number, aur apna sawaal ya samasya bataiye. "
			"Pehle apna naam bataiye."
		),
		"ask_name": "Kripya apna naam bataiye.",
		"ask_bus": "Dhanyavaad. Ab bataiye aap kaunsi gaadi chala rahe hain? Gaadi ka number bataiye.",
		"ask_query": "Ab kripya apna sawaal ya samasya bataiye. Aap poori baat bol sakte hain.",
		"goodbye": "Dhanyavaad. Aapki jaankari darj ho gayi hai. Hum jald hi sampark karenge.",
		"no_input": "Koi jawab nahi mila.",
	},
	"en-IN": {
		"intro": (
			"Thank you. You are connected to Liqui-Connect. "
			"Please tell us your name, vehicle number, and your query or issue. "
			"First, please tell us your name."
		),
		"ask_name": "Please tell us your name.",
		"ask_bus": "Thank you. Which vehicle are you driving? Please tell the vehicle number.",
		"ask_query": "Now please describe your query or issue. You can speak as long as you need.",
		"goodbye": "Thank you. Your query has been recorded. We will get back to you soon.",
		"no_input": "No response received.",
	},
	"ta-IN": {
		"intro": (
			"Nandri. Neenga Liqui-Connect-il inaikkappattulleergal. "
			"Ungal peyar, vaahana number, matrum ungal kelvi allathu pirachanaiyai sollunga. "
			"Mudhalil ungal peyarai sollunga."
		),
		"ask_name": "Ungal peyarai sollunga.",
		"ask_bus": "Nandri. Neenga endha vaahanaththai ottukireergal? Vaahana number sollunga.",
		"ask_query": "Ippoludhu ungal kelvi allathu pirachanaiyai sollunga.",
		"goodbye": "Nandri. Ungal thagaval pathivu seyyappattathu.",
		"no_input": "Badhil varavillai.",
	},
	"te-IN": {
		"intro": (
			"Dhanyavaadaalu. Meeru Liqui-Connect tho anubandhincharu. "
			"Dayachesi mee peru, vaahana number, mariyu mee prasna leda samasya cheppandi. "
			"Modatiga mee peru cheppandi."
		),
		"ask_name": "Dayachesi mee peru cheppandi.",
		"ask_bus": "Dhanyavaadaalu. Meeru ee vaahanaanni naduputhunnaru? Vaahana number cheppandi.",
		"ask_query": "Ipudu dayachesi mee prasna leda samasya cheppandi.",
		"goodbye": "Dhanyavaadaalu. Mee samachaaramu nmodhu seyyabadindi.",
		"no_input": "Samadhanam raledu.",
	},
	"kn-IN": {
		"intro": (
			"Dhanyavaadagalu. Neevu Liqui-Connect ge sambandha horagiddiri. "
			"Dayavittu nimma hesaru, vaahana number, mattu nimma prashne athava samasye heli. "
			"Modalige nimma hesaru heli."
		),
		"ask_name": "Dayavittu nimma hesaru heli.",
		"ask_bus": "Dhanyavaadagalu. Neevu yaava vaahana odisuttiddiri? Vaahana number heli.",
		"ask_query": "Iga dayavittu nimma prashne athava samasye heli.",
		"goodbye": "Dhanyavaadagalu. Nimma mahiti dakhalaagide.",
		"no_input": "Uttara barilla.",
	},
}


def _get_prompts(language):
	return _PROMPTS.get(language, _PROMPTS["hi-IN"])


@frappe.whitelist(allow_guest=True)
def inbound_exoml():
	"""
	Called when a driver calls the Exotel inbound number.
	Creates a Driver Query doc and plays the language selection menu.
	"""
	try:
		frappe.flags.ignore_permissions = True

		settings = frappe.get_single("Voice Ops Settings")
		if not settings.enable_inbound_calls:
			return Response(
				build_goodbye_xml("hi-IN", "Yeh seva abhi uplabdh nahi hai. Dhanyavaad.", provider="Exotel"),
				mimetype="text/xml",
			)

		form = frappe.request.form
		caller_phone = form.get("CallFrom") or form.get("From") or ""
		call_sid = form.get("CallSid") or ""

		# Create Driver Query doc
		dq = frappe.get_doc({
			"doctype": "Driver Query",
			"caller_phone": caller_phone,
			"call_sid": call_sid,
			"status": "Draft",
			"called_at": now_datetime(),
		})
		dq.insert(ignore_permissions=True)
		frappe.db.commit()

		action_url = build_callback_url(
			"voice_ops.api.exotel_webhook.exotel_language_callback",
			driver_query=dq.name,
		)

		prompt_lines = [
			("en-IN", "Welcome to Liqui-Connect."),
			("hi-IN", "Liqui-Connect mein aapka swagat hai."),
			("en-IN", "Press 1 for English."),
			("hi-IN", "Hindi ke liye 2 dabaiye."),
			("ta-IN", "Tamil-kku 3 azhuthavum."),
			("te-IN", "Telugu kosam 4 noppandi."),
			("kn-IN", "Kannada ge 5 odiri."),
		]

		exoml = build_gather_xml(
			prompt_lines=prompt_lines,
			action_url=action_url,
			num_digits=1,
			timeout=10,
			provider="Exotel",
		)

		return Response(exoml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Inbound ExoML Failed")
		return Response(build_error_xml(provider="Exotel"), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def exotel_language_callback():
	"""
	Called after the caller presses a digit to select language (Exotel).
	Stores the language, explains the process, and asks for name.
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		dq_name = args.get("driver_query")
		digits = form.get("digits") or form.get("Digits") or ""

		if not dq_name or not frappe.db.exists("Driver Query", dq_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
				mimetype="text/xml",
			)

		language = _LANG_DIGITS.get(digits, "hi-IN")
		frappe.db.set_value("Driver Query", dq_name, "selected_language", language)
		frappe.db.commit()

		prompts = _get_prompts(language)

		callback_url = build_callback_url(
			"voice_ops.api.exotel_webhook.inbound_exotel_recording_callback",
			driver_query=dq_name, step="name",
		)

		exoml = build_greeting_record_xml(
			language=language,
			greeting_text=prompts["intro"],
			question_text=prompts["ask_name"],
			record_callback_url=callback_url,
			timeout=5,
			max_length=15,
			no_input_text=prompts["no_input"],
			redirect_url=callback_url,
			provider="Exotel",
		)

		return Response(exoml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Exotel Language Callback Failed")
		return Response(build_error_xml(provider="Exotel"), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def inbound_exotel_recording_callback():
	"""
	Steps through inbound query recording: name → bus → query.
	Uses the caller's selected language for all prompts.
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		dq_name = args.get("driver_query")
		step = args.get("step")
		recording_url = form.get("RecordingUrl")

		if not dq_name or not frappe.db.exists("Driver Query", dq_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
				mimetype="text/xml",
			)

		language = frappe.db.get_value("Driver Query", dq_name, "selected_language") or "hi-IN"
		prompts = _get_prompts(language)

		settings = frappe.get_single("Voice Ops Settings")
		query_max_length = settings.inbound_query_max_length or 120

		# Store recording URL for current step
		if recording_url:
			field_map = {
				"name": "name_recording_url",
				"bus": "bus_recording_url",
				"query": "query_recording_url",
			}
			field = field_map.get(step)
			if field:
				frappe.db.set_value("Driver Query", dq_name, field, recording_url)
				frappe.db.commit()

		if step == "name":
			callback_url = build_callback_url(
				"voice_ops.api.exotel_webhook.inbound_exotel_recording_callback",
				driver_query=dq_name, step="bus",
			)
			return Response(
				build_say_record_xml(
					language=language,
					say_text=prompts["ask_bus"],
					record_callback_url=callback_url,
					timeout=5,
					max_length=15,
					no_input_text=prompts["no_input"],
					redirect_url=callback_url,
					provider="Exotel",
				),
				mimetype="text/xml",
			)

		elif step == "bus":
			callback_url = build_callback_url(
				"voice_ops.api.exotel_webhook.inbound_exotel_recording_callback",
				driver_query=dq_name, step="query",
			)
			return Response(
				build_say_record_xml(
					language=language,
					say_text=prompts["ask_query"],
					record_callback_url=callback_url,
					timeout=10,
					max_length=query_max_length,
					no_input_text=prompts["no_input"],
					redirect_url=callback_url,
					provider="Exotel",
				),
				mimetype="text/xml",
			)

		elif step == "query":
			frappe.db.set_value("Driver Query", dq_name, "status", "Recording")
			frappe.enqueue(
				"voice_ops.jobs.process_driver_query.process",
				queue="long",
				driver_query_name=dq_name,
			)
			frappe.db.commit()

			return Response(
				build_goodbye_xml(language, prompts["goodbye"], provider="Exotel"),
				mimetype="text/xml",
			)

		return Response(
			'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
			mimetype="text/xml",
		)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Inbound Exotel Recording Callback Failed")
		return Response(build_error_xml(provider="Exotel"), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False
