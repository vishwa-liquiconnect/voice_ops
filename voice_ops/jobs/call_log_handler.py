"""
Call Log Event Handler

Hooks into exotel_integration's Call Log updates to detect when a
voice ops call completes with a recording. Downloads the recording,
attaches it as a File (auto-uploaded to S3 via frappe_s3_attachment),
then enqueues transcript processing.
"""

import frappe
import requests


def on_call_log_update(doc, method):
	"""
	Called on Call Log on_update (via doc_events hook).

	Checks if this Call Log is linked to a Checklist Run and if the call
	has completed with a recording URL. If so, downloads the recording,
	creates a File attachment (goes to S3 automatically), and enqueues processing.
	"""
	if doc.status != "Completed" or not doc.recording_url:
		return

	checklist_run_name = _get_linked_checklist_run(doc)
	if not checklist_run_name:
		return

	# Idempotency: skip if already processed
	current_status = frappe.db.get_value("Checklist Run", checklist_run_name, "status")
	if current_status in ("Processing", "Evaluated", "Needs Review", "Approved", "Rejected"):
		return

	# Update Checklist Run status
	frappe.db.set_value("Checklist Run", checklist_run_name, {
		"status": "Call Completed",
		"completed_at": frappe.utils.now_datetime(),
	})

	# Enqueue the download + processing as a background job
	# This keeps the webhook handler lightweight per CLAUDE.md
	frappe.enqueue(
		"voice_ops.jobs.process_call_recording.process",
		queue="long",
		call_log_name=doc.name,
		recording_url=doc.recording_url,
		checklist_run_name=checklist_run_name,
	)


def download_and_attach_recording(call_log_name, recording_url):
	"""
	Download recording from Exotel and attach as a File to the Call Log.

	The frappe_s3_attachment app's after_insert hook on File doctype
	will automatically upload it to S3 and replace the local file.

	Returns the File document name, or None on failure.
	"""
	# Download the recording
	audio_content = _download_recording(recording_url)
	if not audio_content:
		return None

	# Determine file extension from URL or default to mp3
	ext = ".mp3"
	if ".wav" in recording_url:
		ext = ".wav"

	file_name = f"call_recording_{call_log_name}{ext}"

	# Create File attachment — frappe_s3_attachment will auto-upload to S3
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"content": audio_content,
		"is_private": 1,
		"attached_to_doctype": "Call Log",
		"attached_to_name": call_log_name,
	})
	file_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return file_doc.name


def _download_recording(recording_url):
	"""Download recording from Exotel with retry."""
	for attempt in range(3):
		try:
			response = requests.get(recording_url, timeout=60)
			response.raise_for_status()
			if len(response.content) < 100:
				# Recording not ready yet (too small)
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


def _get_linked_checklist_run(call_log):
	"""Find a Checklist Run linked to this Call Log."""
	for link in call_log.get("links", []):
		if link.link_doctype == "Checklist Run" and link.link_name:
			if frappe.db.exists("Checklist Run", link.link_name):
				return link.link_name

	return frappe.db.get_value(
		"Checklist Run", {"call_log": call_log.name}, "name"
	)
