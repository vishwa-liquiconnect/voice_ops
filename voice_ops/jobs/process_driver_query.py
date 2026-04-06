"""
Background Job: Process Driver Query

Pipeline for inbound driver query calls:
1. Download all 3 recordings (name, bus, query)
2. Transcribe each via Sarvam AI (translate mode → English)
3. Identify caller from phone number → Trip Crew Member
4. Resolve today's trip → operations_incharge (route manager)
5. Attempt vehicle match from verbal bus info
6. Generate query summary via Claude API
7. Send notifications to MD + route manager

Per CLAUDE.md: All external calls include retry logic. No silent failures.
"""

import frappe
from frappe.utils import now_datetime, today, getdate

from voice_ops.services.query_notification import notify_query
from voice_ops.services.sarvam import transcribe_bytes
from voice_ops.services.summarizer import summarize_query
from voice_ops.services.telephony import download_recording as _download_recording


def process(driver_query_name):
	"""Main background job entry point."""
	try:
		dq = frappe.get_doc("Driver Query", driver_query_name)
		dq.status = "Processing"
		dq.save(ignore_permissions=True)
		frappe.db.commit()

		# Step 1-2: Download and transcribe recordings
		# Use caller's DTMF-selected language as a hint (Sarvam still auto-detects)
		detected_language = dq.selected_language or None

		for field_prefix, recording_field, transcript_field in [
			("name", "name_recording_url", "name_transcript"),
			("bus", "bus_recording_url", "bus_transcript"),
			("query", "query_recording_url", "query_transcript"),
		]:
			recording_url = getattr(dq, recording_field, None)
			if not recording_url:
				continue

			try:
				transcript, lang = _transcribe_recording(recording_url, field_prefix)
				setattr(dq, transcript_field, transcript)
				if lang and not detected_language:
					detected_language = lang
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Voice Ops: Failed transcribing {field_prefix} for {driver_query_name}",
				)

		dq.detected_language = detected_language or ""

		# Store verbal name and bus from transcripts
		dq.verbal_name = (dq.name_transcript or "").strip()
		dq.verbal_bus = (dq.bus_transcript or "").strip()

		# Step 3: Identify caller from phone number
		crew_member = _identify_caller(dq.caller_phone)
		if crew_member:
			dq.crew_member = crew_member

		# Step 4: Resolve today's trip and route manager
		if dq.crew_member:
			trip_info = _resolve_trip(dq.crew_member)
			if trip_info:
				dq.trip_roster_assignment = trip_info.get("name")
				dq.route_manager = trip_info.get("operations_incharge")
				if not dq.vehicle and trip_info.get("vehicle"):
					dq.vehicle = trip_info["vehicle"]

		# Step 5: Attempt vehicle match from verbal bus
		if not dq.vehicle and dq.verbal_bus:
			dq.vehicle = _match_vehicle(dq.verbal_bus)

		# Step 6: Generate summary
		try:
			dq.query_summary = summarize_query(
				dq.query_transcript,
				caller_name=dq.verbal_name or dq.crew_member_name,
				bus_info=dq.verbal_bus or dq.vehicle,
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Summarization failed for {driver_query_name}",
			)
			# Use transcript as fallback
			dq.query_summary = (dq.query_transcript or "")[:500]

		dq.status = "Processed"
		dq.processed_at = now_datetime()
		dq.save(ignore_permissions=True)
		frappe.db.commit()

		# Step 7: Send notifications
		try:
			notify_query(driver_query_name)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Voice Ops: Notification failed for {driver_query_name}",
			)

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Failed processing driver query {driver_query_name}",
		)
		if driver_query_name:
			frappe.db.set_value("Driver Query", driver_query_name, "status", "Failed")
			frappe.db.commit()


def _transcribe_recording(recording_url, prefix):
	"""Download and transcribe a single recording via Sarvam."""
	audio_bytes = _download_recording(recording_url, flow="inbound")
	if not audio_bytes or len(audio_bytes) < 100:
		return "", None

	file_name = f"recording_{prefix}.mp3"
	result = transcribe_bytes(audio_bytes, file_name=file_name)

	transcript = result.get("transcript", "")
	language = result.get("language_code", "")

	return transcript, language


def _identify_caller(phone_number):
	"""
	Look up Trip Crew Member by phone number.

	Matches on last 10 digits to handle country code variations.
	"""
	if not phone_number:
		return None

	# Normalize: strip non-digits, take last 10
	import re
	digits = re.sub(r'\D', '', phone_number)
	if len(digits) < 10:
		return None

	last_10 = digits[-10:]

	# Search active crew members
	crew = frappe.db.sql("""
		SELECT name FROM `tabTrip Crew Member`
		WHERE status = 'Active'
		AND mobile_number LIKE %s
		LIMIT 1
	""", (f"%{last_10}",), as_dict=True)

	return crew[0]["name"] if crew else None


def _resolve_trip(crew_member):
	"""
	Find today's Trip Roster Assignment for a crew member.

	Returns dict with name, operations_incharge, vehicle, or None.
	"""
	current_date = getdate(today())

	trip = frappe.db.sql("""
		SELECT
			tra.name,
			tra.operations_incharge,
			tra.vehicle
		FROM `tabTrip Roster Assignment` tra
		WHERE
			tra.date = %s
			AND tra.docstatus = 1
			AND (tra.driver_1 = %s OR tra.driver_2 = %s)
		ORDER BY tra.modified DESC
		LIMIT 1
	""", (current_date, crew_member, crew_member), as_dict=True)

	return trip[0] if trip else None


def _match_vehicle(verbal_bus):
	"""
	Attempt to match verbal bus description against Vehicle names.

	Tries exact match first, then partial match on the license plate.
	"""
	if not verbal_bus:
		return None

	verbal_clean = verbal_bus.strip().upper()

	# Try exact match
	if frappe.db.exists("Vehicle", verbal_clean):
		return verbal_clean

	# Try partial match (license plate numbers often spoken partially)
	import re
	# Extract alphanumeric sequences from verbal input
	parts = re.findall(r'[A-Z0-9]+', verbal_clean)
	if not parts:
		return None

	search_term = "".join(parts)
	if len(search_term) < 4:
		return None

	vehicles = frappe.db.sql("""
		SELECT name FROM `tabVehicle`
		WHERE REPLACE(REPLACE(name, ' ', ''), '-', '') LIKE %s
		LIMIT 1
	""", (f"%{search_term}%",), as_dict=True)

	return vehicles[0]["name"] if vehicles else None
