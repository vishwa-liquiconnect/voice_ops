"""
Query Summarizer Service

Uses Claude API (Anthropic) to generate concise English summaries
of driver query transcripts. Falls back to simple extraction if
the API is unavailable.
"""

import re

import frappe


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
