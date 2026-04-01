"""
Sarvam AI Service

Handles speech-to-text transcription via Sarvam's speech-to-text-translate API.
Accepts audio files (wav, mp3, etc.) and returns transcript + detected language.

API Reference:
  POST https://api.sarvam.ai/speech-to-text-translate
  Headers: api-subscription-key: <key>
  Body: multipart/form-data with file field

Response:
  {
    "request_id": "...",
    "transcript": "...",
    "language_code": "ta-IN",
    "diarized_transcript": null,
    "language_probability": null
  }
"""

import os
import tempfile

import frappe
import requests


def get_sarvam_settings():
	"""Return Sarvam AI credentials from Voice Ops Settings."""
	settings = frappe.get_single("Voice Ops Settings")
	if not settings.enabled:
		frappe.throw("Voice Ops is not enabled. Please enable it in Voice Ops Settings.")

	return {
		"api_key": settings.get_password("sarvam_api_key"),
		"api_url": settings.sarvam_api_url or "https://api.sarvam.ai",
		"model": settings.sarvam_model or "saarika:v2.5",
		"language_code": settings.sarvam_language_code or "unknown",
	}


def transcribe_file(file_path):
	"""
	Transcribe an audio file using Sarvam AI speech-to-text-translate.

	Args:
		file_path: Absolute path to the audio file on disk

	Returns:
		dict with: transcript, language_code, request_id, raw_response
	"""
	config = get_sarvam_settings()
	url = f"{config['api_url']}/speech-to-text"
	headers = {
		"api-subscription-key": config["api_key"],
	}

	try:
		with open(file_path, "rb") as f:
			files = {"file": (os.path.basename(file_path), f)}
			data = {
				"model": config["model"],
				"language_code": config["language_code"],
			}
			response = requests.post(url, headers=headers, files=files, data=data, timeout=120)
			response.raise_for_status()
			result = response.json()
	except requests.exceptions.RequestException as e:
		frappe.log_error(
			f"Sarvam STT API failed: {e}",
			"Voice Ops: Sarvam Transcription Failed",
		)
		return {
			"transcript": "",
			"language_code": "",
			"request_id": "",
			"raw_response": {"error": str(e)},
		}

	return {
		"transcript": result.get("transcript", ""),
		"language_code": result.get("language_code", ""),
		"request_id": result.get("request_id", ""),
		"raw_response": result,
	}


def transcribe_from_url(audio_url):
	"""
	Download audio from URL, then transcribe via Sarvam.

	Args:
		audio_url: URL to the audio file (e.g. Exotel recording URL)

	Returns:
		dict with: transcript, language_code, request_id, raw_response
	"""
	audio_content = _download_audio(audio_url)
	if not audio_content:
		return {
			"transcript": "",
			"language_code": "",
			"request_id": "",
			"raw_response": {"error": "Failed to download audio"},
		}

	# Write to temp file and transcribe
	suffix = ".mp3" if ".mp3" in audio_url else ".wav"
	with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
		tmp.write(audio_content)
		tmp_path = tmp.name

	try:
		return transcribe_file(tmp_path)
	finally:
		if os.path.exists(tmp_path):
			os.remove(tmp_path)


def transcribe_from_attachment(file_doc_name):
	"""
	Transcribe an audio file stored as a Frappe File attachment (in S3).

	Downloads the file from S3 (via Frappe's file serving) then sends to Sarvam.

	Args:
		file_doc_name: Name of the File document

	Returns:
		dict with: transcript, language_code, request_id, raw_response
	"""
	file_doc = frappe.get_doc("File", file_doc_name)
	file_content = file_doc.get_content()

	if not file_content:
		return {
			"transcript": "",
			"language_code": "",
			"request_id": "",
			"raw_response": {"error": f"Empty file: {file_doc_name}"},
		}

	suffix = os.path.splitext(file_doc.file_name or "audio.wav")[1] or ".wav"
	with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
		tmp.write(file_content)
		tmp_path = tmp.name

	try:
		return transcribe_file(tmp_path)
	finally:
		if os.path.exists(tmp_path):
			os.remove(tmp_path)


def _download_audio(audio_url):
	"""Download audio file from URL with retry."""
	for attempt in range(3):
		try:
			response = requests.get(audio_url, timeout=60)
			response.raise_for_status()
			return response.content
		except requests.exceptions.RequestException as e:
			if attempt == 2:
				frappe.log_error(
					f"Failed to download audio from {audio_url}: {e}",
					"Voice Ops: Audio Download Failed",
				)
				return None
