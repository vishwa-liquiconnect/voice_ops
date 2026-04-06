"""
Twilio Inbound Webhook Handlers for Driver Queries

IVR flow for inbound driver calls:
1. inbound_twiml - Called when driver calls in. Plays language selection menu (DTMF).
2. language_callback - Stores selected language, explains what to say, asks for name.
3. inbound_recording_callback - Steps through name → bus → query recordings.
4. inbound_recording_status - Reliable fallback for recording availability.

All endpoints are allow_guest=True since Twilio calls them without auth.
"""

import frappe
from frappe.utils import now_datetime
from werkzeug.wrappers import Response

from voice_ops.services.telephony import (
	build_callback_url,
	build_error_xml,
	build_gather_xml,
	build_goodbye_xml,
	build_greeting_record_xml,
	build_say_record_xml,
)


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
		"ask_query": "Ippoludhu ungal kelvi allathu pirachanaiyai sollunga. Neenga virumbiyadhu pola pesalaam.",
		"goodbye": "Nandri. Ungal thagaval pathivu seyyappattathu. Naangal vilaivu thodarbu kolgirom.",
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
		"ask_query": "Ipudu dayachesi mee prasna leda samasya cheppandi. Meeru entha sepu aina matladalavacchu.",
		"goodbye": "Dhanyavaadaalu. Mee samachaaramu nmodhu seyyabadindi. Memu tvaraloga sambandhinchukunthamu.",
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
		"ask_query": "Iga dayavittu nimma prashne athava samasye heli. Neevu beku adashtu maathaadabahudu.",
		"goodbye": "Dhanyavaadagalu. Nimma mahiti dakhalaagide. Naavu sheeghradalli samparksutteve.",
		"no_input": "Uttara barilla.",
	},
}


def _get_prompts(language):
	return _PROMPTS.get(language, _PROMPTS["hi-IN"])


@frappe.whitelist(allow_guest=True)
def inbound_twiml():
	"""
	Called when a driver calls the Twilio inbound number.
	Creates a Driver Query document and plays the language selection menu.
	"""
	try:
		frappe.flags.ignore_permissions = True

		settings = frappe.get_single("Voice Ops Settings")
		if not settings.enable_inbound_calls:
			return Response(
				build_goodbye_xml("hi-IN", "Yeh seva abhi uplabdh nahi hai. Dhanyavaad."),
				mimetype="text/xml",
			)

		form = frappe.request.form
		caller_phone = form.get("From") or form.get("Caller") or ""
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

		# Build language selection menu
		action_url = build_callback_url(
			"voice_ops.api.inbound_webhook.language_callback",
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

		twiml = build_gather_xml(
			prompt_lines=prompt_lines,
			action_url=action_url,
			num_digits=1,
			timeout=10,
		)

		return Response(twiml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Inbound TwiML Failed")
		return Response(build_error_xml(), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def language_callback():
	"""
	Called after the caller presses a digit to select language.
	Stores the language, explains the process, and asks for name.
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		dq_name = args.get("driver_query")
		digits = form.get("Digits") or ""

		if not dq_name or not frappe.db.exists("Driver Query", dq_name):
			return Response(
				'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
				mimetype="text/xml",
			)

		# Map digit to language (default to Hindi if invalid/no input)
		language = _LANG_DIGITS.get(digits, "hi-IN")
		frappe.db.set_value("Driver Query", dq_name, "selected_language", language)
		frappe.db.commit()

		prompts = _get_prompts(language)

		# Build callback for name recording
		callback_url = build_callback_url(
			"voice_ops.api.inbound_webhook.inbound_recording_callback",
			driver_query=dq_name, step="name",
		)
		status_callback_url = build_callback_url(
			"voice_ops.api.inbound_webhook.inbound_recording_status",
			driver_query=dq_name, step="name",
		)

		# Intro explains the process, then transitions into asking for name
		twiml = build_greeting_record_xml(
			language=language,
			greeting_text=prompts["intro"],
			question_text=prompts["ask_name"],
			record_callback_url=callback_url,
			status_callback_url=status_callback_url,
			timeout=5,
			max_length=15,
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
def inbound_recording_callback():
	"""
	Called after each inbound recording step.
	Steps through: name → bus → query.
	On final step, enqueues background processing.
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

		# Get selected language for this query
		language = frappe.db.get_value("Driver Query", dq_name, "selected_language") or "hi-IN"
		prompts = _get_prompts(language)

		settings = frappe.get_single("Voice Ops Settings")
		query_max_length = settings.inbound_query_max_length or 120

		# Store recording URL for current step
		if recording_url:
			if not recording_url.endswith((".mp3", ".wav")):
				recording_url = f"{recording_url}.mp3"

			field_map = {
				"name": "name_recording_url",
				"bus": "bus_recording_url",
				"query": "query_recording_url",
			}
			field = field_map.get(step)
			if field:
				frappe.db.set_value("Driver Query", dq_name, field, recording_url)
				frappe.db.commit()

		# Determine next step
		if step == "name":
			callback_url = build_callback_url(
				"voice_ops.api.inbound_webhook.inbound_recording_callback",
				driver_query=dq_name, step="bus",
			)
			status_url = build_callback_url(
				"voice_ops.api.inbound_webhook.inbound_recording_status",
				driver_query=dq_name, step="bus",
			)
			return Response(
				build_say_record_xml(
					language=language,
					say_text=prompts["ask_bus"],
					record_callback_url=callback_url,
					status_callback_url=status_url,
					timeout=5,
					max_length=15,
					no_input_text=prompts["no_input"],
					redirect_url=callback_url,
				),
				mimetype="text/xml",
			)

		elif step == "bus":
			callback_url = build_callback_url(
				"voice_ops.api.inbound_webhook.inbound_recording_callback",
				driver_query=dq_name, step="query",
			)
			status_url = build_callback_url(
				"voice_ops.api.inbound_webhook.inbound_recording_status",
				driver_query=dq_name, step="query",
			)
			return Response(
				build_say_record_xml(
					language=language,
					say_text=prompts["ask_query"],
					record_callback_url=callback_url,
					status_callback_url=status_url,
					timeout=10,
					max_length=query_max_length,
					no_input_text=prompts["no_input"],
					redirect_url=callback_url,
				),
				mimetype="text/xml",
			)

		elif step == "query":
			# All recordings done — enqueue processing
			frappe.db.set_value("Driver Query", dq_name, "status", "Recording")
			frappe.enqueue(
				"voice_ops.jobs.process_driver_query.process",
				queue="long",
				driver_query_name=dq_name,
			)
			frappe.db.commit()

			return Response(
				build_goodbye_xml(language, prompts["goodbye"]),
				mimetype="text/xml",
			)

		# Unknown step
		return Response(
			'<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
			mimetype="text/xml",
		)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Inbound Recording Callback Failed")
		return Response(build_error_xml(), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def inbound_recording_status():
	"""
	Reliable fallback: called by Twilio when the recording file is ready.
	Stores recording URL if not already stored (idempotent).
	"""
	try:
		frappe.flags.ignore_permissions = True

		args = frappe.request.args
		form = frappe.request.form

		dq_name = args.get("driver_query")
		step = args.get("step")
		recording_url = form.get("RecordingUrl")
		rec_status = form.get("RecordingStatus")

		if not dq_name or not recording_url:
			return

		if rec_status and rec_status != "completed":
			return

		if not recording_url.endswith((".mp3", ".wav")):
			recording_url = f"{recording_url}.mp3"

		if not frappe.db.exists("Driver Query", dq_name):
			return

		field_map = {
			"name": "name_recording_url",
			"bus": "bus_recording_url",
			"query": "query_recording_url",
		}
		field = field_map.get(step)
		if not field:
			return

		# Only store if not already set (idempotent)
		current_value = frappe.db.get_value("Driver Query", dq_name, field)
		if not current_value:
			frappe.db.set_value("Driver Query", dq_name, field, recording_url)
			frappe.db.commit()

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Inbound Recording Status Failed")
	finally:
		frappe.flags.ignore_permissions = False
