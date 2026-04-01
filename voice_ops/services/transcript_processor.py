"""
Transcript Processor

Normalizes raw transcripts into structured checklist responses.
Handles multilingual yes/no detection, numeric extraction, and free text mapping.
"""

import re


# Language-specific synonym maps for yes/no detection
YES_SYNONYMS = {
	"hi-IN": {"haan", "ha", "ji", "ji haan", "bilkul", "theek", "theek hai", "sahi", "ho"},
	"en-IN": {"yes", "yeah", "yep", "correct", "right", "ok", "okay", "sure", "affirmative"},
	"ta-IN": {"aam", "aama", "sari", "seri"},
	"te-IN": {"avunu", "anu", "sare"},
	"kn-IN": {"haudu", "howdu", "sari"},
}

NO_SYNONYMS = {
	"hi-IN": {"nahi", "naa", "na", "nahi hai", "mat", "bilkul nahi"},
	"en-IN": {"no", "nope", "not", "negative", "nah"},
	"ta-IN": {"illa", "illai", "vendam"},
	"te-IN": {"ledu", "kadu", "vaddu"},
	"kn-IN": {"illa", "alla", "beda"},
}


def process_transcript(transcript_text, template_questions, language_code=None):
	"""
	Process a raw transcript against template questions.

	For MVP, this assumes a single-call flow where the transcript covers
	all questions sequentially. Each question's response is extracted
	by splitting the transcript into segments.

	Args:
		transcript_text: Raw transcript string from Sarvam
		template_questions: List of dicts with question_key, expected_response_type, etc.
		language_code: Language code (e.g. "hi-IN")

	Returns:
		List of dicts with: question_key, raw_transcript, normalized_response, confidence
	"""
	language_code = language_code or "hi-IN"

	if not transcript_text or not template_questions:
		return []

	# Split transcript into segments (one per question)
	segments = _split_transcript(transcript_text, len(template_questions))

	results = []
	for i, question in enumerate(template_questions):
		segment = segments[i] if i < len(segments) else ""
		segment = segment.strip()

		normalized = normalize_response(
			segment,
			question.get("expected_response_type", "Yes/No"),
			language_code,
			select_options=question.get("select_options"),
		)

		results.append({
			"question_key": question["question_key"],
			"raw_transcript": segment,
			"normalized_response": str(normalized) if normalized is not None else "",
			"confidence": _estimate_segment_confidence(segment, normalized),
		})

	return results


def _split_transcript(transcript_text, num_questions):
	"""
	Split transcript into segments, one per question.

	For MVP, uses simple sentence/pause splitting. In production,
	this could use Sarvam's structured output or speaker diarization.
	"""
	# Split on common sentence boundaries
	parts = re.split(r'[.।?\n]+', transcript_text)
	parts = [p.strip() for p in parts if p.strip()]

	if len(parts) >= num_questions:
		# If we have enough parts, distribute them
		return parts[:num_questions]

	if len(parts) == 1 and num_questions == 1:
		return parts

	# If fewer parts than questions, try splitting on commas
	if len(parts) < num_questions:
		expanded = []
		for part in parts:
			sub_parts = [s.strip() for s in part.split(",") if s.strip()]
			expanded.extend(sub_parts)
		if len(expanded) >= num_questions:
			return expanded[:num_questions]

	# Pad with empty strings if we still don't have enough
	while len(parts) < num_questions:
		parts.append("")

	return parts[:num_questions]


def normalize_response(text, response_type, language_code, select_options=None):
	"""
	Normalize a transcript segment into a structured value.

	Returns:
		For Yes/No: True, False, or None
		For Numeric: int/float or None
		For Select: matched option or None
		For Free Text: cleaned text
	"""
	if not text:
		return None

	text_lower = text.lower().strip()

	if response_type == "Yes/No":
		return normalize_yes_no(text_lower, language_code)
	elif response_type == "Numeric":
		return normalize_numeric(text_lower)
	elif response_type == "Select":
		return normalize_select(text_lower, select_options)
	else:
		return text.strip()


def normalize_yes_no(text, language_code):
	"""
	Detect yes/no from text in the given language.

	Returns True for yes, False for no, None if unclear.
	"""
	# Check all language variants (driver may code-switch)
	for lang in [language_code, "en-IN", "hi-IN"]:
		yes_words = YES_SYNONYMS.get(lang, set())
		no_words = NO_SYNONYMS.get(lang, set())

		if text in yes_words:
			return True
		if text in no_words:
			return False

	# Partial match: check if any yes/no word appears in the text
	all_yes = set()
	all_no = set()
	for lang_words in YES_SYNONYMS.values():
		all_yes.update(lang_words)
	for lang_words in NO_SYNONYMS.values():
		all_no.update(lang_words)

	words = set(text.split())
	yes_matches = words & all_yes
	no_matches = words & all_no

	if yes_matches and not no_matches:
		return True
	if no_matches and not yes_matches:
		return False

	return None


def normalize_numeric(text):
	"""Extract a number from text."""
	# Direct number
	numbers = re.findall(r'[\d.]+', text)
	if numbers:
		try:
			val = float(numbers[0])
			return int(val) if val == int(val) else val
		except (ValueError, OverflowError):
			return None
	return None


def normalize_select(text, select_options):
	"""Match text against select options."""
	if not select_options:
		return None

	options = [opt.strip().lower() for opt in select_options.split("\n") if opt.strip()]
	original_options = [opt.strip() for opt in select_options.split("\n") if opt.strip()]

	# Exact match
	for i, opt in enumerate(options):
		if text == opt:
			return original_options[i]

	# Partial/fuzzy match
	for i, opt in enumerate(options):
		if opt in text or text in opt:
			return original_options[i]

	return None


def _estimate_segment_confidence(segment, normalized):
	"""
	Estimate confidence for a transcript segment.

	This is a simple heuristic. The actual confidence from Sarvam
	applies to the whole transcript; this estimates per-segment quality.
	"""
	if not segment:
		return 0.0

	if normalized is None:
		return 0.3  # Could not normalize — low confidence

	# Short, clear responses get high confidence
	word_count = len(segment.split())
	if word_count <= 3:
		return 0.9
	elif word_count <= 8:
		return 0.7
	else:
		return 0.5  # Longer responses may contain noise
