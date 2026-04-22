"""
Feedback Call API

Two entry points:

1. `trigger_feedback_call` — whitelisted trigger used by UI buttons or
   automation. Delegates to `voice_ops.services.feedback_call`.

2. `feedback_exoml` + `feedback_recording_callback` — allow_guest
   endpoints Exotel fetches over HTTP during the call. Used when the
   feedback flow runs via dynamic ExoML (`Url` mode) instead of the
   static App flow (`App` mode).
"""

import frappe
from werkzeug.wrappers import Response

from voice_ops.services.feedback_call import initiate_feedback_call
from voice_ops.services.telephony import build_callback_url, build_error_xml


@frappe.whitelist()
def trigger_feedback_call(to_number=None, employee=None,
                          reference_doctype=None, reference_name=None):
	"""
	Trigger an outbound Exotel feedback call.

	Provide either `to_number` directly or an `employee` (Employee ID) to
	resolve a phone number from. Optionally link a reference doctype +
	name on the Call Log for context.
	"""
	call_log_name = initiate_feedback_call(
		to_number=to_number,
		employee=employee,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
	return {"call_log": call_log_name, "status": "Call Initiated"}


@frappe.whitelist(allow_guest=True)
def feedback_exoml():
	"""
	ExoML endpoint Exotel fetches when a feedback call connects.

	Plays the configured audio prompt (or falls back to TTS) then records
	the driver's response. Exotel POSTs the recording metadata to
	`feedback_recording_callback` via the `action` attribute.
	"""
	try:
		frappe.flags.ignore_permissions = True

		settings = frappe.get_single("Voice Ops Settings")
		prompt_url = (settings.feedback_prompt_audio_url or "").strip()
		max_length = settings.max_recording_length or 60
		timeout = settings.default_response_timeout or 5

		call_sid = (
			(frappe.request.form.get("CallSid") if frappe.request.form else None)
			or frappe.request.args.get("CallSid")
			or ""
		)

		callback_url = build_callback_url(
			"voice_ops.api.feedback.feedback_recording_callback",
			call_sid=call_sid,
		)

		if prompt_url:
			prompt_line = f"<Play>{prompt_url}</Play>"
		else:
			prompt_line = "<Say>Hello. Please share your feedback after the beep. Thank you.</Say>"

		exoml = (
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<Response>'
			f'{prompt_line}'
			f'<Record action="{callback_url}" maxLength="{max_length}" timeout="{timeout}" />'
			'<Hangup/>'
			'</Response>'
		)
		return Response(exoml, mimetype="text/xml")

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Voice Ops: Feedback ExoML failed")
		return Response(build_error_xml(provider="Exotel"), mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False


@frappe.whitelist(allow_guest=True)
def feedback_recording_callback():
	"""
	Exotel POSTs here after the feedback recording ends.

	Stores the RecordingUrl on the matching Call Log via a real .save()
	so the existing `attach_exotel_recording` on_update hook picks it up
	and runs the download + transcription pipeline. Returns a Hangup
	response to terminate the call.
	"""
	hangup = (
		'<?xml version="1.0" encoding="UTF-8"?>'
		'<Response><Hangup/></Response>'
	)

	try:
		frappe.flags.ignore_permissions = True

		form = frappe.request.form or {}
		args = frappe.request.args or {}

		call_sid = args.get("call_sid") or form.get("CallSid") or ""
		recording_url = form.get("RecordingUrl") or ""

		if call_sid and recording_url and frappe.db.exists("Call Log", call_sid):
			call_log = frappe.get_doc("Call Log", call_sid)
			if not call_log.recording_url:
				call_log.recording_url = recording_url
				call_log.save(ignore_permissions=True)
				frappe.db.commit()

		return Response(hangup, mimetype="text/xml")

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"Voice Ops: Feedback recording callback failed",
		)
		return Response(hangup, mimetype="text/xml")
	finally:
		frappe.flags.ignore_permissions = False
