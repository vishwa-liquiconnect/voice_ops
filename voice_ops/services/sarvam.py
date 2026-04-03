"""
Sarvam AI Service

Handles speech-to-text transcription via Sarvam's REST API.

API Reference:
  POST https://api.sarvam.ai/speech-to-text/transcribe
  Headers: api-subscription-key: <key>
  Body: multipart/form-data with file, model, language_code

Response:
  {
    "request_id": "...",
    "transcript": "...",
    "language_code": "ta-IN"
  }

Note: REST API supports audio up to ~30s. For longer recordings,
the batch API (sarvamai SDK) should be used — see transcribe_bytes_batch().
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


def _get_mime_type(file_name):
	"""Determine MIME type from file name."""
	ext = os.path.splitext(file_name)[1].lower()
	return {
		".mp3": "audio/mpeg",
		".wav": "audio/wav",
		".ogg": "audio/ogg",
		".flac": "audio/flac",
		".m4a": "audio/mp4",
	}.get(ext, "audio/wav")


def transcribe_file(file_path):
	"""
	Transcribe an audio file on disk via Sarvam REST API.

	Args:
		file_path: Absolute path to the audio file

	Returns:
		dict with: transcript, language_code, request_id, raw_response
	"""
	config = get_sarvam_settings()
	url = f"{config['api_url']}/speech-to-text/transcribe"
	headers = {
		"api-subscription-key": config["api_key"],
	}

	try:
		with open(file_path, "rb") as f:
			mime = _get_mime_type(file_path)
			files = {"file": (os.path.basename(file_path), f, mime)}
			data = {
				"model": config["model"],
				"language_code": config["language_code"],
			}
			response = requests.post(url, headers=headers, files=files, data=data, timeout=120)
			if not response.ok:
				error_detail = response.text
				frappe.log_error(
					f"Sarvam STT API returned {response.status_code}: {error_detail}",
					"Voice Ops: Sarvam Transcription Failed",
				)
				return {
					"transcript": "",
					"language_code": "",
					"request_id": "",
					"raw_response": {"error": error_detail, "status_code": response.status_code},
				}
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


def transcribe_bytes(audio_bytes, file_name="audio.wav"):
	"""
	Transcribe raw audio bytes via Sarvam REST API.

	Args:
		audio_bytes: Raw audio content (bytes)
		file_name: Filename hint for the API (determines MIME type)

	Returns:
		dict with: transcript, language_code, request_id, raw_response
	"""
	if not audio_bytes:
		return {
			"transcript": "",
			"language_code": "",
			"request_id": "",
			"raw_response": {"error": "Empty audio bytes"},
		}

	config = get_sarvam_settings()
	url = f"{config['api_url']}/speech-to-text/transcribe"
	headers = {
		"api-subscription-key": config["api_key"],
	}

	try:
		mime = _get_mime_type(file_name)
		files = {"file": (file_name, audio_bytes, mime)}
		data = {
			"model": config["model"],
			"language_code": config["language_code"],
		}
		response = requests.post(url, headers=headers, files=files, data=data, timeout=120)
		if not response.ok:
			error_detail = response.text
			frappe.log_error(
				f"Sarvam STT API returned {response.status_code}: {error_detail}",
				"Voice Ops: Sarvam Transcription Failed",
			)
			return {
				"transcript": "",
				"language_code": "",
				"request_id": "",
				"raw_response": {"error": error_detail, "status_code": response.status_code},
			}
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
		audio_url: URL to the audio file (e.g. Twilio recording URL)

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

	file_name = "recording.mp3" if ".mp3" in audio_url else "recording.wav"
	return transcribe_bytes(audio_content, file_name=file_name)


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
