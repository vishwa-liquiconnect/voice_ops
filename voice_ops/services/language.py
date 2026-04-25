"""
Driver Language Resolution + Translation

Resolution chain, in order:
  1. Call Log `custom_detected_language` — Sarvam's detected code,
     persisted at transcription time.
  2. Voice Ops Settings `default_driver_language` — fallback code.
  3. "en-IN".

Translation uses the same Claude client as the summarizer. English
prompts are translated into the target language for WhatsApp acks.
"""

import frappe


LANGUAGE_NAMES = {
	"hi-IN": "Hindi",
	"ta-IN": "Tamil",
	"te-IN": "Telugu",
	"kn-IN": "Kannada",
	"ml-IN": "Malayalam",
	"mr-IN": "Marathi",
	"bn-IN": "Bengali",
	"gu-IN": "Gujarati",
	"pa-IN": "Punjabi",
	"ur-IN": "Urdu",
	"od-IN": "Odia",
	"en-IN": "English",
}


CHECKLIST_DTMF_LANGUAGES = {
	"1": "ta-IN",
	"2": "en-IN",
	"3": "hi-IN",
	"4": "te-IN",
	"5": "kn-IN",
}


CHECKLIST_SYSTEM_PROMPTS = {
	"en-IN": {
		"intro": "Hello. Your checklist is starting.",
		"no_input": "No response received.",
		"no_input_next": "No response. Next question.",
		"goodbye": "Thank you. Your checklist is complete.",
	},
	"hi-IN": {
		"intro": "नमस्ते। आपकी चेकलिस्ट शुरू हो रही है।",
		"no_input": "कोई जवाब नहीं मिला।",
		"no_input_next": "कोई जवाब नहीं मिला। अगला सवाल।",
		"goodbye": "धन्यवाद। आपकी चेकलिस्ट पूरी हो गई है।",
	},
	"ta-IN": {
		"intro": "வணக்கம். உங்கள் சரிபார்ப்புப் பட்டியல் தொடங்குகிறது.",
		"no_input": "பதில் கிடைக்கவில்லை.",
		"no_input_next": "பதில் கிடைக்கவில்லை. அடுத்த கேள்வி.",
		"goodbye": "நன்றி. உங்கள் சரிபார்ப்புப் பட்டியல் முடிந்தது.",
	},
	"te-IN": {
		"intro": "నమస్కారం. మీ చెక్‌లిస్ట్ ప్రారంభమవుతుంది.",
		"no_input": "సమాధానం లేదు.",
		"no_input_next": "సమాధానం లేదు. తదుపరి ప్రశ్న.",
		"goodbye": "ధన్యవాదాలు. మీ చెక్‌లిస్ట్ పూర్తయింది.",
	},
	"kn-IN": {
		"intro": "ನಮಸ್ಕಾರ. ನಿಮ್ಮ ಚೆಕ್‌ಲಿಸ್ಟ್ ಪ್ರಾರಂಭವಾಗುತ್ತಿದೆ.",
		"no_input": "ಉತ್ತರ ಬರಲಿಲ್ಲ.",
		"no_input_next": "ಉತ್ತರ ಬರಲಿಲ್ಲ. ಮುಂದಿನ ಪ್ರಶ್ನೆ.",
		"goodbye": "ಧನ್ಯವಾದಗಳು. ನಿಮ್ಮ ಚೆಕ್‌ಲಿಸ್ಟ್ ಪೂರ್ಣಗೊಂಡಿದೆ.",
	},
}


CHECKLIST_MENU_PROMPT_LINES = [
	("en-IN", "Welcome. Please select your language."),
	("ta-IN", "தமிழுக்கு 1 ஐ அழுத்தவும்."),
	("en-IN", "Press 2 for English."),
	("hi-IN", "हिंदी के लिए 3 दबाएं।"),
	("te-IN", "తెలుగు కోసం 4 నొక్కండి."),
	("kn-IN", "ಕನ್ನಡಕ್ಕಾಗಿ 5 ಒತ್ತಿರಿ."),
]


def checklist_system_prompts(language_code):
	"""Return greeting/no-input/goodbye phrases for the IVR shell."""
	return CHECKLIST_SYSTEM_PROMPTS.get(language_code, CHECKLIST_SYSTEM_PROMPTS["hi-IN"])


def resolve_driver_language(call_log):
	"""Return a BCP-47-ish language code for the driver of this call."""
	code = (call_log.get("custom_detected_language") or "").strip()
	if code:
		return code

	try:
		default = frappe.db.get_single_value("Voice Ops Settings", "default_driver_language") or ""
	except Exception:
		default = ""
	default = (default or "").strip()
	if default:
		return default

	return "en-IN"


def localized_question_text(question, language_code):
	"""Return a question's prompt text in `language_code`.

	Order of preference:
	  1. English question text when `language_code` is en-IN.
	  2. Hand-written `question_text_hi` when language is hi-IN and present.
	  3. Cached Claude translation of the English text, keyed by
	     `question_key + language_code`. First call per (key, language)
	     hits Claude; subsequent calls are free.
	  4. Falls back to English on any failure (live IVR must not stall).
	"""
	english_text = (getattr(question, "question_text", None) or "").strip()
	if not english_text:
		return ""

	code = (language_code or "").strip() or "en-IN"
	if code.startswith("en"):
		return english_text

	if code == "hi-IN":
		hindi = (getattr(question, "question_text_hi", None) or "").strip()
		if hindi:
			return hindi

	question_key = (getattr(question, "question_key", None) or "").strip()
	cache_field = f"{code}:{question_key}" if question_key else None
	cache = frappe.cache()
	if cache_field:
		hit = cache.hget("voice_ops:question_translation", cache_field)
		if hit:
			return hit if isinstance(hit, str) else hit.decode("utf-8")

	translated = translate_to(english_text, code) or english_text
	if cache_field and translated and translated != english_text:
		cache.hset("voice_ops:question_translation", cache_field, translated)
	return translated


def translate_to(text, language_code):
	"""Translate English `text` to `language_code` via Claude.

	Falls back to the original text when Claude isn't configured or
	the call fails — WhatsApp still goes out, just in English.
	"""
	if not text or not language_code:
		return text
	if language_code.startswith("en"):
		return text

	from voice_ops.services.summarizer import _get_anthropic_key, _get_model, fms_ai_available

	if not fms_ai_available():
		return text

	api_key = _get_anthropic_key()
	if not api_key:
		return text

	try:
		import anthropic
	except ImportError:
		return text

	language_name = LANGUAGE_NAMES.get(language_code, language_code)
	prompt = (
		f"Translate the following message into {language_name}. "
		"Preserve ticket IDs (like ISS-2026-00004) verbatim. "
		"Return only the translated text, no commentary.\n\n"
		f"Message:\n{text}"
	)

	try:
		client = anthropic.Anthropic(api_key=api_key)
		message = client.messages.create(
			model=_get_model(),
			max_tokens=256,
			messages=[{"role": "user", "content": prompt}],
		)
		translated = (message.content[0].text or "").strip()
		return translated or text
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voice Ops: Translation to {language_code} failed",
		)
		return text
