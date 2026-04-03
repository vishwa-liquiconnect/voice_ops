"""
Call Recording Handler

Downloads recordings from Twilio and attaches them as Files
(auto-uploaded to S3 via frappe_s3_attachment).
"""

import frappe
import requests


def download_and_attach_recording(twilio_log_name, recording_url, audio_bytes=None):
	"""
	Attach recording as a File to the Twilio Call Log.

	Args:
		twilio_log_name: Twilio Call Log document name
		recording_url: Twilio recording URL (used for download if audio_bytes not provided)
		audio_bytes: Pre-downloaded audio content. If None, downloads from recording_url.

	Returns the File document name, or None on failure.
	"""
	audio_content = audio_bytes or _download_recording(recording_url)
	if not audio_content:
		return None

	ext = ".mp3"
	if ".wav" in recording_url:
		ext = ".wav"

	file_name = f"call_recording_{twilio_log_name}{ext}"

	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"content": audio_content,
		"is_private": 1,
		"attached_to_doctype": "Twilio Call Log",
		"attached_to_name": twilio_log_name,
	})
	file_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return file_doc.name


def _download_recording(recording_url):
	"""Download recording from Twilio with auth and retry."""
	auth = _get_twilio_auth(recording_url)

	for attempt in range(3):
		try:
			response = requests.get(recording_url, auth=auth, timeout=60)
			response.raise_for_status()
			if len(response.content) < 100:
				if attempt < 2:
					import time
					time.sleep(2)
					continue
			return response.content
		except requests.exceptions.RequestException as e:
			if attempt == 2:
				frappe.log_error(
					f"Failed to download recording from {recording_url}: {e}",
					"Voice Ops: Recording Download Failed",
				)
				return None


def _get_twilio_auth(recording_url):
	"""Get Twilio HTTP Basic Auth if the URL is a Twilio API URL."""
	if "api.twilio.com" not in recording_url:
		return None

	from requests.auth import HTTPBasicAuth
	settings = frappe.get_single("Twilio Settings")
	return HTTPBasicAuth(
		settings.account_sid,
		settings.get_password("auth_token"),
	)
