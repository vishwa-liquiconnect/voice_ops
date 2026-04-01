"""
Rule Evaluator

Deterministic business rule evaluation for checklist responses.
All rules are explicit, testable, and auditable per CLAUDE.md.

AI must NOT make business decisions. This module implements all
decision logic using config-driven rules.

Rules are organized per the Driver Voice System Detailed SOP:
- Pre-Boarding Checklist (~50 parameters)
- Post-Trip Checklist (~25 parameters)
- Action & Escalation Matrix (9 triggers)
"""

import frappe
from frappe.utils import now_datetime


# ---------------------------------------------------------------------------
# PRE-BOARDING: Hard blocker rules (block departure)
# ---------------------------------------------------------------------------
BLOCKER_RULES = {
	# Crew & Identity
	"driver_1": {
		"blocker_when_false": True,
		"label": "Driver 1 not available",
		"escalation": "block_trip",
	},
	"driver_fitness": {
		"blocker_when_false": True,
		"label": "Driver not fit for duty",
		"escalation": "block_trip",
	},
	"driver_2": {
		"blocker_when_false": True,
		"label": "Driver 2 not available",
		"escalation": "crew_missing",
	},
	"crew_at_end_point": {
		"blocker_when_false": True,
		"label": "Crew not confirmed till destination",
		"escalation": "crew_missing",
	},

	# Documents
	"documents_available": {
		"blocker_when_false": True,
		"label": "Required documents (RC/Insurance/FC/Permit/PUC) missing",
		"escalation": "document_missing",
	},

	# Mechanical & Safety
	"mechanical_issue": {
		"blocker_when_true": True,
		"label": "Mechanical issue reported",
		"escalation": "block_vehicle",
	},
	"electrical_issue": {
		"blocker_when_true": True,
		"label": "Electrical issue reported",
		"escalation": "block_vehicle",
	},

	# Safety equipment
	"door_locks": {
		"blocker_when_false": True,
		"label": "Door locks not working",
		"escalation": "safety_missing",
	},
	"emergency_exit": {
		"blocker_when_false": True,
		"label": "Emergency exit not accessible",
		"escalation": "safety_missing",
	},
	"fire_extinguisher": {
		"blocker_when_false": True,
		"label": "Fire extinguisher not available or not charged",
		"escalation": "safety_missing",
	},
	"safety_equipment": {
		"blocker_when_false": True,
		"label": "Safety equipment missing or non-functional",
		"escalation": "safety_missing",
	},

	# Tyres
	"tyres": {
		"blocker_when_false": True,
		"label": "Tyre condition not acceptable",
		"escalation": "block_vehicle",
	},

	# Incident
	"incident_report": {
		"blocker_when_true": True,
		"label": "Incident or breakdown reported",
		"escalation": "incident",
	},

	# Final status
	"ready_status": {
		"blocker_when_false": True,
		"label": "Explicit not-ready status",
		"escalation": "block_trip",
	},

	# --- Post-Trip blockers ---
	"incident": {
		"blocker_when_true": True,
		"label": "Incident/breakdown/accident reported post-trip",
		"escalation": "incident",
	},
	"next_trip_ready": {
		"blocker_when_false": True,
		"label": "Vehicle not ready for next trip",
		"escalation": "block_vehicle",
	},
	"mechanical_complaint": {
		"blocker_when_true": True,
		"label": "Mechanical complaint reported post-trip",
		"escalation": "block_vehicle",
	},
	"tyre_issue": {
		"blocker_when_true": True,
		"label": "Tyre issue reported post-trip",
		"escalation": "block_vehicle",
	},
}


# ---------------------------------------------------------------------------
# PRE-BOARDING + POST-TRIP: Soft warning rules (noted, don't block)
# ---------------------------------------------------------------------------
WARNING_RULES = {
	# Pre-boarding: Crew
	"cleaner": {
		"warning_when_false": True,
		"label": "Cleaner not available",
	},

	# Pre-boarding: AC & Electronics
	"ac_working": {
		"warning_when_false": True,
		"label": "AC not working properly",
		"escalation": "maintenance_ticket",
	},
	"ac_condition": {
		"warning_when_false": True,
		"label": "AC issue reported",
		"escalation": "maintenance_ticket",
	},
	"charging_points": {
		"warning_when_false": True,
		"label": "Charging points not working",
	},
	"inverter": {
		"warning_when_false": True,
		"label": "Inverter not working",
	},
	"lights": {
		"warning_when_false": True,
		"label": "Lights not working",
	},
	"speaker": {
		"warning_when_false": True,
		"label": "Speaker not working",
	},
	"amplifier": {
		"warning_when_false": True,
		"label": "Amplifier not working",
	},
	"mic": {
		"warning_when_false": True,
		"label": "MIC not working",
	},

	# Pre-boarding: Cleaning
	"interior_cleaning": {
		"warning_when_false": True,
		"label": "Interior cleaning not done",
	},
	"seat_cleaning": {
		"warning_when_false": True,
		"label": "Seat cleaning not done",
	},
	"washroom_clean": {
		"warning_when_false": True,
		"label": "Washroom not clean",
	},
	"room_freshener": {
		"warning_when_false": True,
		"label": "Room freshener not applied",
	},
	"bad_smell": {
		"warning_when_true": True,
		"label": "Bad smell detected",
	},
	"cleaning_status": {
		"warning_when_false": True,
		"label": "Cleaning incomplete",
	},
	"cleaning_kit": {
		"warning_when_false": True,
		"label": "Cleaning kit not available",
	},

	# Pre-boarding: Linen
	"curtains_clean": {
		"warning_when_false": True,
		"label": "Curtains not clean",
	},
	"blankets_clean": {
		"warning_when_false": True,
		"label": "Blankets not clean",
	},
	"bedsheets_clean": {
		"warning_when_false": True,
		"label": "Bedsheets not clean",
	},
	"linen_due": {
		"warning_when_true": True,
		"label": "Linen wash overdue",
		"escalation": "linen_overdue",
	},

	# Pre-boarding: Supplies
	"water_bottles": {
		"warning_when_false": True,
		"label": "Water bottles not stocked",
	},
	"washroom_water": {
		"warning_when_false": True,
		"label": "Washroom water not filled",
	},
	"washroom_exhaust": {
		"warning_when_false": True,
		"label": "Washroom exhaust not working",
	},
	"supplies_status": {
		"warning_when_false": True,
		"label": "Low supplies",
	},

	# Pre-boarding: Tools
	"hammer": {
		"warning_when_false": True,
		"label": "Hammer not available",
	},
	"toolkit": {
		"warning_when_false": True,
		"label": "Toolkit not available",
	},
	"stepney": {
		"warning_when_false": True,
		"label": "Stepney not available",
	},
	"jack": {
		"warning_when_false": True,
		"label": "Jack not available",
	},
	"lever": {
		"warning_when_false": True,
		"label": "Lever not available",
	},

	# Pre-boarding: Vehicle exterior
	"branding": {
		"warning_when_false": True,
		"label": "Branding not OK",
	},

	# --- Post-Trip warnings ---
	"delay": {
		"warning_when_true": True,
		"label": "Trip was delayed",
	},
	"crew_issue": {
		"warning_when_true": True,
		"label": "Crew issue reported post-trip",
	},
	"electrical_complaint": {
		"warning_when_true": True,
		"label": "Electrical complaint reported post-trip",
	},
	"ac_performance": {
		"warning_when_false": True,
		"label": "AC performance issue post-trip",
		"escalation": "maintenance_ticket",
	},
	"interior_feedback": {
		"warning_when_false": True,
		"label": "Interior feedback negative",
	},
	"washroom_feedback": {
		"warning_when_false": True,
		"label": "Washroom feedback negative",
	},
	"linen_status": {
		"warning_when_false": True,
		"label": "Linen not washed",
	},
	"service_due": {
		"warning_when_true": True,
		"label": "Vehicle service due",
	},
	"charging_issue": {
		"warning_when_true": True,
		"label": "Charging issue reported post-trip",
	},
	"inverter_issue": {
		"warning_when_true": True,
		"label": "Inverter issue reported post-trip",
	},
	"speaker_issue": {
		"warning_when_true": True,
		"label": "Speaker issue reported post-trip",
	},
	"passenger_complaint": {
		"warning_when_true": True,
		"label": "Passenger complaint received",
	},
}


# ---------------------------------------------------------------------------
# Analytics flags: tracked for pattern detection
# ---------------------------------------------------------------------------
FLAG_RULES = {
	"diesel_level": {
		"flag_when_low": True,
		"threshold": 25,
		"label": "Diesel level below threshold",
	},
	"diesel_exception": {
		"flag_when_true": True,
		"label": "Diesel exception reported",
		"escalation": "diesel_excess",
	},
	"idling_high": {
		"flag_when_true": True,
		"label": "High idling reported",
	},
	"exterior_damage": {
		"flag_when_true": True,
		"label": "Exterior damage reported",
	},
	"extra_blankets": {
		"flag_when_low": True,
		"threshold": 5,
		"label": "Extra blankets below minimum (5)",
	},
}


# ---------------------------------------------------------------------------
# Free-text capture fields (no pass/fail, just stored for record)
# These question_keys are recognized but always pass evaluation.
# ---------------------------------------------------------------------------
CAPTURE_ONLY_KEYS = {
	"vehicle_number", "route", "service_id", "vehicle_location",
	"documents_shared", "ac_issue_details", "last_wash_date",
	"blanket_count", "extra_water", "exterior_damage_details",
	# Post-trip capture
	"destination_time", "delay_reason", "driver_1_end", "driver_2_end",
	"cleaner_end", "ac_details", "service_commitment", "diesel_reason",
	"idling_reason", "driving_behaviour", "comments",
}


def evaluate_checklist(checklist_run_name):
	"""
	Main entry point: evaluate all responses in a Checklist Run.

	Loads the run, applies rules to each response, computes overall result,
	and updates the document.
	"""
	run = frappe.get_doc("Checklist Run", checklist_run_name)

	blocker_count = 0
	warning_count = 0
	flags = []
	notes = []
	escalation_triggers = []

	settings = frappe.get_single("Voice Ops Settings")
	confidence_threshold = settings.low_confidence_threshold or 0.7

	for response in run.responses:
		result = evaluate_response(
			response.question_key,
			response.normalized_response,
			response.confidence or 0.0,
			response.is_blocker,
			response.is_warning,
			confidence_threshold,
		)

		response.evaluation_result = result["result"]
		response.evaluation_note = result.get("note", "")
		response.is_blocker = 1 if result["result"] == "Blocker" else response.is_blocker
		response.is_warning = 1 if result["result"] == "Warning" else 0

		if result["result"] == "Blocker":
			blocker_count += 1
			notes.append(f"BLOCKER: {result.get('note', response.question_key)}")
			if result.get("escalation"):
				escalation_triggers.append({
					"type": result["escalation"],
					"question_key": response.question_key,
					"label": result.get("note", ""),
				})
		elif result["result"] == "Warning":
			warning_count += 1
			notes.append(f"WARNING: {result.get('note', response.question_key)}")
			if result.get("escalation"):
				escalation_triggers.append({
					"type": result["escalation"],
					"question_key": response.question_key,
					"label": result.get("note", ""),
				})
		elif result["result"] == "Flag":
			flags.append(result.get("note", response.question_key))
			if result.get("escalation"):
				escalation_triggers.append({
					"type": result["escalation"],
					"question_key": response.question_key,
					"label": result.get("note", ""),
				})
		elif result["result"] == "Needs Review":
			notes.append(f"REVIEW: {result.get('note', response.question_key)}")

	# Compute overall result
	overall = compute_overall_result(blocker_count, warning_count)
	needs_review = should_require_review(run, confidence_threshold)

	# Update the run
	run.blocker_count = blocker_count
	run.warning_count = warning_count
	run.flags = ", ".join(flags) if flags else ""
	run.evaluation_notes = "\n".join(notes) if notes else ""
	run.overall_result = overall
	run.evaluated_at = now_datetime()
	run.needs_review = 1 if needs_review else 0

	if needs_review:
		run.status = "Needs Review"
		run.review_reason = _build_review_reason(run, confidence_threshold)
	else:
		run.status = "Evaluated"

	run.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"overall_result": overall,
		"blocker_count": blocker_count,
		"warning_count": warning_count,
		"needs_review": needs_review,
		"escalation_triggers": escalation_triggers,
	}


def evaluate_response(question_key, normalized_response, confidence, is_blocker_question, is_warning_question, confidence_threshold):
	"""
	Evaluate a single response against business rules.

	Returns dict with: result (Pass/Blocker/Warning/Flag/Needs Review), note, escalation
	"""
	# Capture-only fields always pass
	if question_key in CAPTURE_ONLY_KEYS:
		return {"result": "Pass", "note": ""}

	# Low confidence always triggers review
	if confidence < confidence_threshold and confidence > 0:
		return {
			"result": "Needs Review",
			"note": f"Low confidence ({confidence:.2f}) for {question_key}",
		}

	# Empty response on required question
	if not normalized_response:
		return {
			"result": "Needs Review",
			"note": f"No response captured for {question_key}",
		}

	# Parse boolean response
	bool_value = _parse_bool(normalized_response)

	# Check hard blocker rules
	if question_key in BLOCKER_RULES:
		rule = BLOCKER_RULES[question_key]
		if rule.get("blocker_when_false") and bool_value is False:
			return {
				"result": "Blocker",
				"note": rule["label"],
				"escalation": rule.get("escalation"),
			}
		if rule.get("blocker_when_true") and bool_value is True:
			return {
				"result": "Blocker",
				"note": rule["label"],
				"escalation": rule.get("escalation"),
			}

	# Check if the question is marked as a potential blocker in the template
	if is_blocker_question and bool_value is False:
		return {
			"result": "Blocker",
			"note": f"Negative response on blocker question: {question_key}",
		}

	# Check warning rules
	if question_key in WARNING_RULES:
		rule = WARNING_RULES[question_key]
		if rule.get("warning_when_false") and bool_value is False:
			return {
				"result": "Warning",
				"note": rule["label"],
				"escalation": rule.get("escalation"),
			}
		if rule.get("warning_when_true") and bool_value is True:
			return {
				"result": "Warning",
				"note": rule["label"],
				"escalation": rule.get("escalation"),
			}

	# Check if the question is marked as a warning in the template
	if is_warning_question and bool_value is False:
		return {
			"result": "Warning",
			"note": f"Negative response on warning question: {question_key}",
		}

	# Check flag rules
	if question_key in FLAG_RULES:
		rule = FLAG_RULES[question_key]
		if rule.get("flag_when_true") and bool_value is True:
			return {
				"result": "Flag",
				"note": rule["label"],
				"escalation": rule.get("escalation"),
			}
		if rule.get("flag_when_low"):
			try:
				numeric_val = float(normalized_response)
				if numeric_val < rule.get("threshold", 0):
					return {
						"result": "Flag",
						"note": rule["label"],
						"escalation": rule.get("escalation"),
					}
			except (ValueError, TypeError):
				pass

	return {"result": "Pass", "note": ""}


def compute_overall_result(blocker_count, warning_count):
	"""Compute overall checklist result. Deterministic."""
	if blocker_count > 0:
		return "Fail"
	if warning_count > 0:
		return "Warning"
	return "Pass"


def should_require_review(checklist_run, confidence_threshold):
	"""Determine if a Checklist Run needs manual review."""
	# Any blocker always needs review
	if checklist_run.blocker_count and checklist_run.blocker_count > 0:
		return True

	# Any low-confidence response needs review
	for response in checklist_run.responses:
		if (response.confidence or 0) < confidence_threshold and (response.confidence or 0) > 0:
			return True
		if response.evaluation_result == "Needs Review":
			return True

	# Any empty response needs review
	for response in checklist_run.responses:
		if not response.normalized_response:
			return True

	return False


def _build_review_reason(checklist_run, confidence_threshold):
	"""Build a human-readable review reason."""
	reasons = []

	if checklist_run.blocker_count and checklist_run.blocker_count > 0:
		reasons.append(f"{checklist_run.blocker_count} blocker(s) detected")

	low_confidence = [
		r.question_key
		for r in checklist_run.responses
		if (r.confidence or 0) < confidence_threshold and (r.confidence or 0) > 0
	]
	if low_confidence:
		reasons.append(f"Low confidence on: {', '.join(low_confidence)}")

	missing = [r.question_key for r in checklist_run.responses if not r.normalized_response]
	if missing:
		reasons.append(f"Missing responses: {', '.join(missing)}")

	return "; ".join(reasons) if reasons else "Manual review required"


def _parse_bool(value):
	"""Parse a string value to boolean."""
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		if value.lower() in ("true", "1", "yes"):
			return True
		if value.lower() in ("false", "0", "no"):
			return False
	return None
