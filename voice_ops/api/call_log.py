"""
Call Log API.

Two whitelisted endpoints used from the desk:

- `stream_recording` — proxies a Twilio/Exotel recording through Frappe
  so the browser never sees the upstream HTTP Basic Auth prompt. Used
  by `public/js/timeline_recording_proxy.js` and the Call Log form's
  rewritten <audio> player.
- `transcribe_recording` — manual re-trigger from the Call Log form to
  re-run Sarvam on the attached recording (e.g. when the original
  voicemail-ingest job failed). Returns the transcript; the caller
  writes it into `summary`.
"""

from urllib.parse import urlparse

import frappe


@frappe.whitelist()
def stream_recording(call_log=None, url=None):
	"""Proxy a call recording through Frappe so the browser never sees
	the upstream Basic Auth challenge (Exotel/Twilio).

	Pass either a Call Log name (recommended — looks up recording_url)
	or a direct recording URL. Only whitelisted hosts are allowed.
	"""
	from voice_ops.services.telephony import _download_exotel_recording, _download_twilio_recording

	recording_url = url
	if call_log:
		recording_url = frappe.db.get_value("Call Log", call_log, "recording_url")

	if not recording_url:
		frappe.throw("No recording URL found")

	host = urlparse(recording_url).netloc.lower()
	allowed_hosts = (
		"recordings.exotel.com",
		"s3-ap-southeast-1.amazonaws.com",
		"api.twilio.com",
	)
	if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
		frappe.throw(f"Host not allowed: {host}")

	if "exotel" in host:
		audio = _download_exotel_recording(recording_url)
	else:
		audio = _download_twilio_recording(recording_url)

	if not audio:
		frappe.throw("Failed to fetch the recording")

	is_mp3 = ".mp3" in recording_url.lower()
	file_name = recording_url.rsplit("/", 1)[-1] or ("recording.mp3" if is_mp3 else "recording.wav")

	frappe.local.response.filename = file_name
	frappe.local.response.filecontent = audio
	frappe.local.response.type = "download"
	frappe.local.response.display_content_as = "inline"


@frappe.whitelist()
def transcribe_recording(call_log_name):
	"""Transcribe a Call Log recording to English and return the transcript.

	The caller writes this into the `summary` field. Claude summarization is
	handled at digest time, not here.
	"""
	doc = frappe.get_doc("Call Log", call_log_name)
	if not doc.recording_url:
		frappe.throw("No recording URL found on this Call Log.")

	from voice_ops.services.telephony import _download_exotel_recording
	from voice_ops.services.sarvam import transcribe_bytes

	audio = _download_exotel_recording(doc.recording_url)
	if not audio:
		frappe.throw("Failed to download the recording.")

	file_name = "recording.mp3" if ".mp3" in doc.recording_url else "recording.wav"
	result = transcribe_bytes(audio, file_name=file_name)
	transcript = (result.get("transcript") or "").strip()

	if not transcript:
		frappe.throw("Transcription returned empty result.")

	return {"transcript": transcript}
