"""
Sarvam AI Service

Handles speech-to-text transcription via Sarvam's REST API (audio <= 30s)
and batch SDK API (audio > 30s, up to 1 hour).

REST API Reference:
  POST https://api.sarvam.ai/speech-to-text/transcribe
  Headers: api-subscription-key: <key>
  Body: multipart/form-data with file, model, language_code

Batch API: Uses the sarvamai SDK for job-based async processing.
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
		"model": settings.sarvam_model or "saaras:v3",
		"language_code": settings.sarvam_language_code or "unknown",
		"mode": settings.sarvam_mode or "translate",
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
	url = f"{config['api_url']}/speech-to-text-translate" if config.get("mode") == "translate" else f"{config['api_url']}/speech-to-text"
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
				"mode": config["mode"],
			}
			response = requests.post(url, headers=headers, files=files, data=data, timeout=120)
			if not response.ok:
				error_detail = response.text
				frappe.log_error(
					title="Voice Ops: Sarvam STT Failed",
					message=f"File transcribe status {response.status_code}: {error_detail}",
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
			title="Voice Ops: Sarvam STT Failed",
			message=f"File transcribe request error: {e}",
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
	url = f"{config['api_url']}/speech-to-text-translate" if config.get("mode") == "translate" else f"{config['api_url']}/speech-to-text"
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
			# Fall back to batch API for audio exceeding 30s limit
			if response.status_code == 400 and "maximum limit" in error_detail.lower():
				return transcribe_bytes_batch(audio_bytes, file_name=file_name)
			frappe.log_error(
				title="Voice Ops: Sarvam STT Failed",
				message=f"Status {response.status_code}: {error_detail}",
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
			title="Voice Ops: Sarvam STT Failed",
			message=f"Bytes transcribe request error: {e}",
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


def transcribe_bytes_batch(audio_bytes, file_name="audio.wav"):
	"""
	Transcribe audio via Sarvam batch SDK API (for audio > 30s).

	Writes bytes to a temp file, creates a batch job, waits for completion,
	and returns the transcript.

	Args:
		audio_bytes: Raw audio content (bytes)
		file_name: Filename hint (determines extension)

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

	try:
		from sarvamai import SarvamAI

		client = SarvamAI(api_subscription_key=config["api_key"])

		ext = os.path.splitext(file_name)[1] or ".wav"
		with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
			tmp.write(audio_bytes)
			tmp_path = tmp.name

		try:
			job = client.speech_to_text_job.create_job(
				model=config["model"],
				mode=config.get("mode", "translate"),
				language_code=config["language_code"],
			)
			job.upload_files(file_paths=[tmp_path])
			job.start()
			job.wait_until_complete()

			file_results = job.get_file_results()
			if file_results.get("successful"):
				# Download outputs to a temp dir and read the transcript
				with tempfile.TemporaryDirectory() as out_dir:
					job.download_outputs(output_dir=out_dir)
					# Batch API writes JSON files to output dir
					import json
					for f in os.listdir(out_dir):
						if f.endswith(".json"):
							with open(os.path.join(out_dir, f)) as jf:
								result = json.load(jf)
							transcript = result.get("transcript", "")
							if not transcript and result.get("diarized_transcript"):
								entries = result["diarized_transcript"].get("entries", [])
								transcript = " ".join(e.get("transcript", "") for e in entries)
							return {
								"transcript": transcript,
								"language_code": result.get("language_code", ""),
								"request_id": result.get("request_id", ""),
								"raw_response": result,
							}

			error_msg = str(file_results.get("failed", "Unknown batch error"))
			frappe.log_error(
				title="Voice Ops: Sarvam Batch STT Failed",
				message=error_msg,
			)
			return {
				"transcript": "",
				"language_code": "",
				"request_id": "",
				"raw_response": {"error": error_msg},
			}
		finally:
			os.unlink(tmp_path)

	except Exception as e:
		frappe.log_error(
			title="Voice Ops: Sarvam Batch STT Failed",
			message=f"Batch transcribe error: {e}\n{frappe.get_traceback()}",
		)
		return {
			"transcript": "",
			"language_code": "",
			"request_id": "",
			"raw_response": {"error": str(e)},
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
