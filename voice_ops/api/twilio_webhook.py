"""
Twilio Webhook Handlers for Voice Ops

1. twiml_response - Returns TwiML when call is answered (greeting + record)
2. recording_callback - Called when recording is ready, triggers processing pipeline
"""

import frappe
from frappe.utils import get_url
from werkzeug.wrappers import Response


@frappe.whitelist(allow_guest=True)
def twiml_response():
	"""
	Return TwiML instructions when Twilio connects the call.

	Plays a greeting and records the driver's response.
	The recording callback points back to our recording_callback endpoint.
	"""
	from twilio_integration.twilio_integration.doctype.twilio_call_log.twilio_call_log import force_https
	site_url = get_url()
	recording_callback_url = force_https(f"{site_url}/api/method/voice_ops.api.twilio_webhook.recording_callback")

	twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
	<Say language="hi-IN">Namaste. Kripya apni checklist ke sawaalon ka jawab dein. Recording shuru ho rahi hai.</Say>
	<Pause length="1"/>
	<Record maxLength="300" playBeep="true" action="{recording_callback_url}" recordingStatusCallback="{recording_callback_url}" recordingStatusCallbackMethod="POST" />
	<Say language="hi-IN">Dhanyavaad. Aapka jawab record ho gaya hai.</Say>
</Response>"""

	return Response(twiml, mimetype="text/xml")


@frappe.whitelist(allow_guest=True)
def recording_callback():
	"""
	Handle Twilio recording callback.

	Called via <Record action="..."> when recording finishes, and also
	via recordingStatusCallback when the recording file is ready.
	Finds the linked Checklist Run and enqueues transcript processing.

	Must return TwiML since Twilio expects it from the action URL.
	"""
	data = frappe.form_dict
	call_sid = data.get("CallSid")
	recording_url = data.get("RecordingUrl")

	if not call_sid or not recording_url:
		# Return thank-you TwiML even if we can't process
		return Response(
			'<?xml version="1.0" encoding="UTF-8"?><Response><Say language="hi-IN">Dhanyavaad.</Say></Response>',
			mimetype="text/xml",
		)

	# Twilio returns URL without extension — append .mp3
	if not recording_url.endswith((".mp3", ".wav")):
		recording_url = f"{recording_url}.mp3"

	# Find the Twilio Call Log by call_sid
	twilio_log_name = frappe.db.get_value(
		"Twilio Call Log", {"call_sid": call_sid, "type": "Call"}, "name"
	)

	if twilio_log_name:
		# Find linked Checklist Run
		checklist_run_name = frappe.db.get_value(
			"Twilio Call Log", twilio_log_name, "reference_name"
		)

		if checklist_run_name and frappe.db.exists("Checklist Run", checklist_run_name):
			# Idempotency: skip if already processed
			current_status = frappe.db.get_value("Checklist Run", checklist_run_name, "status")
			if current_status not in ("Processing", "Evaluated", "Needs Review", "Approved", "Rejected"):
				frappe.db.set_value("Checklist Run", checklist_run_name, {
					"status": "Call Completed",
					"completed_at": frappe.utils.now_datetime(),
				})

				frappe.enqueue(
					"voice_ops.jobs.process_call_recording.process",
					queue="long",
					twilio_log_name=twilio_log_name,
					recording_url=recording_url,
					checklist_run_name=checklist_run_name,
				)

		frappe.db.commit()

	return Response(
		'<?xml version="1.0" encoding="UTF-8"?><Response><Say language="hi-IN">Dhanyavaad. Aapka jawab record ho gaya hai.</Say></Response>',
		mimetype="text/xml",
	)
