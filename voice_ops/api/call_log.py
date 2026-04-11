import frappe


@frappe.whitelist()
def summarize_recording(call_log_name):
	"""
	Transcribe a Call Log recording to English and generate a Claude
	summary (if the Anthropic key is configured). Returns both so the
	client can populate `transcript` and `summary` fields.
	"""
	doc = frappe.get_doc("Call Log", call_log_name)
	if not doc.recording_url:
		frappe.throw("No recording URL found on this Call Log.")

	from voice_ops.services.telephony import _download_exotel_recording
	from voice_ops.services.sarvam import transcribe_bytes
	from voice_ops.services.summarizer import summarize_voicemail

	audio = _download_exotel_recording(doc.recording_url)
	if not audio:
		frappe.throw("Failed to download the recording.")

	file_name = "recording.mp3" if ".mp3" in doc.recording_url else "recording.wav"
	result = transcribe_bytes(audio, file_name=file_name)
	transcript = (result.get("transcript") or "").strip()

	if not transcript:
		frappe.throw("Transcription returned empty result.")

	summary = summarize_voicemail(transcript)

	return {"transcript": transcript, "summary": summary or transcript}
