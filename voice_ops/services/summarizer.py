"""
Query Summarizer Service

Uses Claude API (Anthropic) to generate concise English summaries
of driver query transcripts and voicemails. Falls back to simple
extraction if the API is unavailable.
"""

import re

import frappe


def has_anthropic_key():
	"""Return True if an Anthropic API key is configured in Voice Ops Settings."""
	return bool(_get_anthropic_key())


def summarize_voicemail(transcript, caller_info=None):
	"""
	Generate a concise summary of a voicemail transcript.

	Uses Claude API when an Anthropic key is configured, otherwise returns
	the raw transcript so callers can decide to keep it as-is.

	Args:
		transcript: English transcript of the voicemail
		caller_info: Optional caller/contact context string

	Returns:
		Summary string (1-3 sentences) or the raw transcript on fallback.
	"""
	if not transcript or not transcript.strip():
		return ""

	api_key = _get_anthropic_key()
	if not api_key:
		return transcript.strip()

	try:
		return _summarize_voicemail_with_claude(transcript, caller_info, api_key)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"Voice Ops: Claude voicemail summarization failed, using raw transcript",
		)
		return transcript.strip()


def _summarize_voicemail_with_claude(transcript, caller_info, api_key):
	"""Use Claude API to generate a voicemail summary."""
	import anthropic

	client = anthropic.Anthropic(api_key=api_key)

	prompt = (
		"Summarize the following voicemail in 1-3 concise sentences in English. "
		"Capture the caller's intent, any vehicle/route identifiers mentioned, "
		"and any action requested. Be direct and factual. "
		"If the voicemail is empty or unintelligible, say so briefly.\n"
	)
	if caller_info:
		prompt += f"\nCaller context: {caller_info}\n"
	prompt += f"\nVoicemail transcript:\n{transcript}"

	message = client.messages.create(
		model="claude-haiku-4-5-20251001",
		max_tokens=256,
		messages=[{"role": "user", "content": prompt}],
	)

	return message.content[0].text.strip()


def summarize_voicemail_digest(voicemails):
	"""Produce one cumulative summary across a batch of voicemail transcripts.

	Each voicemail is a dict with at least `from`, `creation`, and `summary`
	(which holds the raw transcript at digest time). Returns an empty string
	when Claude isn't configured or on failure — the caller can then skip
	the overall-summary section.
	"""
	if not voicemails:
		return ""

	api_key = _get_anthropic_key()
	if not api_key:
		return ""

	try:
		return _summarize_voicemail_digest_with_claude(voicemails, api_key)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"Voice Ops: Claude digest summarization failed",
		)
		return ""


_DEFAULT_DIGEST_CONTEXT = (
	"You are an AI assistant for a bus-fleet operations team. "
	"You review voicemails left by drivers and callers reporting "
	"vehicle issues, accidents, complaints, and operational concerns."
)


def _get_digest_context():
	"""Pull the business context from FMS AI Settings.

	Returns the assembled system prompt when the settings doc has any
	meaningful fields populated; otherwise returns an empty string so the
	caller can fall back to the hardcoded default.
	"""
	try:
		settings = frappe.get_single("FMS AI Settings")
	except Exception:
		return ""

	has_content = bool(
		(settings.get("nature_of_business") or "").strip()
		or (settings.get("business_description") or "").strip()
		or settings.get("context_rules")
		or settings.get("doctype_references")
	)
	if not has_content:
		return ""

	try:
		return (settings.build_system_prompt() or "").strip()
	except Exception:
		return ""


def _summarize_voicemail_digest_with_claude(voicemails, api_key):
	import anthropic
	from frappe.utils import format_datetime

	client = anthropic.Anthropic(api_key=api_key)

	system_prompt = _get_digest_context() or _DEFAULT_DIGEST_CONTEXT

	lines = []
	for i, vm in enumerate(voicemails, 1):
		number = vm.get("from") or "Unknown"
		name = vm.get("caller_name")
		designation = vm.get("caller_designation")
		vehicle = vm.get("caller_vehicle")
		when = format_datetime(vm.get("creation"), "yyyy-MM-dd HH:mm") if vm.get("creation") else ""
		transcript = (vm.get("summary") or "").strip() or "(no transcript)"

		if name:
			role = f" ({designation})" if designation else ""
			veh = f" on vehicle {vehicle}" if vehicle else ""
			caller = f"{name}{role}{veh} (phone {number})"
		else:
			caller = f"phone {number}"

		lines.append(f"{i}. [{when}] {caller}: {transcript}")

	user_prompt = (
		f"Review this batch of {len(voicemails)} voicemails and write ONE concise overall summary "
		"(3-6 sentences) that synthesizes the batch. Capture: "
		"(1) the most urgent or critical issues, "
		"(2) vehicles or routes mentioned repeatedly, "
		"(3) any recurring themes or complaints, and "
		"(4) total volume and rough breakdown. "
		"Do not list each voicemail individually. Be factual, direct, and brief.\n\n"
		"Voicemails:\n" + "\n".join(lines)
	)

	message = client.messages.create(
		model="claude-haiku-4-5-20251001",
		max_tokens=512,
		system=_cached_system(system_prompt),
		messages=[{"role": "user", "content": user_prompt}],
	)
	return message.content[0].text.strip()


def _cached_system(system_prompt):
	"""Wrap the system prompt as a cacheable content block.

	The FMS AI Settings prompt is ~4.7k static tokens shared across every
	digest / issue call. Using `cache_control: ephemeral` drops cached-input
	cost to 10% of base. Short prompts below Anthropic's minimum simply
	won't cache (no harm)."""
	return [
		{
			"type": "text",
			"text": system_prompt,
			"cache_control": {"type": "ephemeral"},
		}
	]


def generate_issue_payload(call_log_info, summary):
	"""Ask Claude to turn a Call Log summary into an Issue payload.

	Returns a dict with keys `subject`, `description`, `priority` (one of
	Low/Medium/High) on success, or None when Claude isn't configured,
	the call fails, or the response can't be parsed as JSON.

	`call_log_info` is a dict-ish with at least `name`, `from`,
	`type_of_call`, `duration`, and optionally caller enrichment keys.
	"""
	if not summary or not summary.strip():
		return None

	api_key = _get_anthropic_key()
	if not api_key:
		return None

	try:
		return _generate_issue_payload_with_claude(call_log_info, summary, api_key)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"Voice Ops: Claude issue generation failed",
		)
		return None


def _generate_issue_payload_with_claude(call_log_info, summary, api_key):
	import json as _json

	import anthropic

	client = anthropic.Anthropic(api_key=api_key)
	system_prompt = _get_digest_context() or _DEFAULT_DIGEST_CONTEXT

	name = call_log_info.get("caller_name") or ""
	designation = call_log_info.get("caller_designation") or ""
	vehicle = call_log_info.get("caller_vehicle") or ""
	caller_bits = []
	if name:
		caller_bits.append(name)
	if designation:
		caller_bits.append(f"({designation})")
	if vehicle:
		caller_bits.append(f"vehicle {vehicle}")
	caller_line = " ".join(caller_bits) or "Unknown caller"

	user_prompt = (
		"Convert the following call summary into a support Issue ticket.\n\n"
		f"Caller: {caller_line}\n"
		f"Phone: {call_log_info.get('from') or 'Unknown'}\n"
		f"Call type: {call_log_info.get('type_of_call') or 'Unknown'}\n"
		f"Duration (s): {call_log_info.get('duration') or 0}\n\n"
		f"Summary / Transcript:\n{summary}\n\n"
		"Return ONLY a JSON object with exactly these keys:\n"
		"  subject: short title, max ~80 chars\n"
		"  description: clear, factual description, 2-4 sentences\n"
		"  priority: one of Low, Medium, High\n"
		"  level: one of Vehicle, Office\n"
		"  vehicle_hint: the vehicle identifier actually mentioned in the\n"
		"    transcript (e.g. '2833', 'TN02BY2234', 'bus 4107'). Return an\n"
		"    empty string if no vehicle is mentioned. Do NOT use the\n"
		"    caller's usually-assigned vehicle — only what the transcript\n"
		"    explicitly references.\n"
		"Use High for safety issues, breakdowns, accidents, or incidents. "
		"Use Medium for operational problems needing prompt action. "
		"Use Low for routine feedback or informational messages.\n"
		"Use Vehicle when the call is about a bus / driver / trip / on-road issue. "
		"Use Office when the call is about payments, admin, HR, or back-office matters.\n"
		"Respond with JSON only, no commentary, no code fences."
	)

	message = client.messages.create(
		model="claude-haiku-4-5-20251001",
		max_tokens=512,
		system=_cached_system(system_prompt),
		messages=[{"role": "user", "content": user_prompt}],
	)
	text = _strip_code_fences((message.content[0].text or "").strip())
	try:
		payload = _json.loads(text)
	except Exception:
		frappe.log_error(
			f"Voice Ops: Claude issue payload not valid JSON:\n{text}",
			"Voice Ops: Issue payload parse failed",
		)
		return None

	if not isinstance(payload, dict):
		return None
	return payload


def _strip_code_fences(text):
	if not text.startswith("```"):
		return text
	text = text.split("\n", 1)[1] if "\n" in text else text[3:]
	if text.rstrip().endswith("```"):
		text = text.rstrip()[:-3]
	return text.strip()


def summarize_query(query_transcript, caller_name=None, bus_info=None):
	"""
	Generate a concise summary of a driver query transcript.

	Uses Claude API for intelligent summarization, with a simple
	extractive fallback if the API call fails.

	Args:
		query_transcript: English transcript of the driver's query
		caller_name: Name of the caller (optional, for context)
		bus_info: Bus/vehicle info (optional, for context)

	Returns:
		Summary string (2-3 sentences)
	"""
	if not query_transcript or not query_transcript.strip():
		return "No query recorded."

	# Try Claude API first
	api_key = _get_anthropic_key()
	if api_key:
		try:
			return _summarize_with_claude(query_transcript, caller_name, bus_info, api_key)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				"Voice Ops: Claude summarization failed, using fallback",
			)

	# Fallback: simple extractive summary
	return _extractive_summary(query_transcript)


def _get_anthropic_key():
	"""Get Anthropic API key from Voice Ops Settings."""
	try:
		return frappe.get_single("Voice Ops Settings").get_password("anthropic_api_key")
	except Exception:
		return None


def _summarize_with_claude(transcript, caller_name, bus_info, api_key):
	"""Use Claude API to generate a summary."""
	import anthropic

	client = anthropic.Anthropic(api_key=api_key)

	context_parts = []
	if caller_name:
		context_parts.append(f"Caller: {caller_name}")
	if bus_info:
		context_parts.append(f"Bus: {bus_info}")
	context = ". ".join(context_parts)

	prompt = (
		"Summarize the following driver query in 2-3 concise sentences in English. "
		"Include the key issue or request. Be direct and factual.\n"
	)
	if context:
		prompt += f"\nContext: {context}\n"
	prompt += f"\nQuery transcript:\n{transcript}"

	message = client.messages.create(
		model="claude-haiku-4-5-20251001",
		max_tokens=256,
		messages=[{"role": "user", "content": prompt}],
	)

	return message.content[0].text.strip()


def _extractive_summary(transcript, max_sentences=3):
	"""
	Simple extractive summary: return the first N sentences.

	For short transcripts, returns the full text.
	"""
	transcript = transcript.strip()
	sentences = re.split(r'[.!?]+', transcript)
	sentences = [s.strip() for s in sentences if s.strip()]

	if len(sentences) <= max_sentences:
		return transcript

	return ". ".join(sentences[:max_sentences]) + "."
