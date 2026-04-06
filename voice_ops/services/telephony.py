"""
Telephony Provider Abstraction

Provides a unified interface for Twilio and Exotel telephony operations:
call initiation, XML response building, recording download, and call log management.

The active provider is configured in Voice Ops Settings (telephony_provider field).
"""

import frappe
import requests
from frappe.utils import get_url


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def get_provider():
	"""Return the configured telephony provider ('Twilio' or 'Exotel')."""
	return frappe.db.get_single_value("Voice Ops Settings", "telephony_provider") or "Twilio"


def get_call_log_doctype():
	"""Return the Call Log doctype name for the active provider."""
	if get_provider() == "Exotel":
		return "Call Log"
	return "Twilio Call Log"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def force_https(url):
	"""Ensure a URL uses HTTPS (required by telephony providers for webhooks)."""
	if url and url.startswith("http://"):
		return url.replace("http://", "https://", 1)
	return url


def build_callback_url(endpoint, **params):
	"""Build an HTTPS callback URL with query params, XML-escaped for embedding in XML."""
	site_url = get_url()
	base = force_https(f"{site_url}/api/method/{endpoint}")
	if params:
		query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
		raw = f"{base}?{query}"
	else:
		raw = base
	return raw.replace("&", "&amp;")


# ---------------------------------------------------------------------------
# Call initiation
# ---------------------------------------------------------------------------

def initiate_call(to_number, twiml_url, reference_doctype=None, reference_name=None):
	"""
	Initiate an outbound call via the configured provider.

	Args:
		to_number: Recipient phone number
		twiml_url: URL the provider should fetch for call flow instructions
		reference_doctype: DocType to link in call log
		reference_name: Document name to link

	Returns:
		Call log document name (str)
	"""
	provider = get_provider()
	if provider == "Exotel":
		return _initiate_exotel_call(to_number, twiml_url, reference_doctype, reference_name)
	return _initiate_twilio_call(to_number, twiml_url, reference_doctype, reference_name)


def _initiate_twilio_call(to_number, twiml_url, reference_doctype, reference_name):
	"""Initiate call via Twilio integration app."""
	from twilio_integration.twilio_integration.doctype.twilio_call_log.twilio_call_log import (
		initiate_twilio_call,
		normalize_mobile_no,
	)

	to_number = normalize_mobile_no(to_number)
	twiml_url = force_https(twiml_url)

	result = initiate_twilio_call(
		to_number=to_number,
		twiml_url=twiml_url,
		purpose="Voice Ops Checklist",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
	return result.get("log")


def _initiate_exotel_call(to_number, callback_url, reference_doctype, reference_name):
	"""
	Initiate call via Exotel API with a dynamic flow URL.

	Exotel's Calls/connect API supports a 'Url' parameter that points to
	our ExoML endpoint, similar to Twilio's twiml_url.
	"""
	settings = frappe.get_single("Exotel Settings")
	if not settings.enabled:
		frappe.throw("Exotel integration is not enabled.")

	endpoint = (
		f"https://{settings.api_key}:{settings.get_password('api_token')}"
		f"@api.exotel.com/v1/Accounts/{settings.account_sid}/Calls/connect.json?details=true"
	)

	callback_url = force_https(callback_url)
	exophone = _get_exophone()

	response = requests.post(
		endpoint,
		data={
			"From": exophone,
			"To": to_number,
			"CallerId": exophone,
			"Url": callback_url,
			"Record": "true",
			"StatusCallback": _get_exotel_status_callback_url(),
			"StatusCallbackEvents[0]": "terminal",
			"StatusCallbackEvents[1]": "answered",
		},
		timeout=30,
	)
	response.raise_for_status()

	call_data = response.json().get("Call", {})
	call_sid = call_data.get("Sid")

	# Create Call Log entry (Exotel uses ERPNext's built-in Call Log)
	from exotel_integration.handler import create_call_log

	call_log = create_call_log(
		call_id=call_sid,
		from_number=exophone,
		to_number=to_number,
		medium=exophone,
		call_type="Outgoing",
		link_to_document={
			"link_doctype": reference_doctype,
			"link_name": reference_name,
		} if reference_doctype and reference_name else None,
	)
	return call_log.name


def _get_exophone():
	"""Get the first available Exotel phone number."""
	from exotel_integration.handler import get_all_exophones

	phones = get_all_exophones()
	if not phones:
		frappe.throw("No Exotel phone numbers (exophones) configured.")
	return phones[0]


def _get_exotel_status_callback_url():
	"""Build the Exotel status callback URL with webhook key."""
	webhook_key = frappe.db.get_single_value("Exotel Settings", "webhook_key")
	site_url = get_url()
	return force_https(
		f"{site_url}/api/method/exotel_integration.handler.handle_request?key={webhook_key}"
	)


# ---------------------------------------------------------------------------
# XML response builders
# ---------------------------------------------------------------------------

def build_gather_xml(prompt_lines, action_url, num_digits=1, timeout=10):
	"""
	Build provider-specific XML for DTMF digit collection.

	Args:
		prompt_lines: List of (language, text) tuples to say inside the Gather
		action_url: URL to POST the collected digits to
		num_digits: Number of digits to collect
		timeout: Seconds to wait for input
	"""
	provider = get_provider()
	if provider == "Exotel":
		return _build_exoml_gather(prompt_lines, action_url, num_digits, timeout)
	return _build_twiml_gather(prompt_lines, action_url, num_digits, timeout)


def _build_twiml_gather(prompt_lines, action_url, num_digits, timeout):
	say_tags = "\n\t\t".join(
		f'<Say language="{lang}">{text}</Say>' for lang, text in prompt_lines
	)
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Gather action="{action_url}" numDigits="{num_digits}" timeout="{timeout}">
\t\t{say_tags}
\t</Gather>
\t<Redirect>{action_url}</Redirect>
</Response>"""


def _build_exoml_gather(prompt_lines, action_url, num_digits, timeout):
	say_tags = "\n\t\t".join(f'<Say>{text}</Say>' for _, text in prompt_lines)
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Gather action="{action_url}" numDigits="{num_digits}" timeout="{timeout}">
\t\t{say_tags}
\t</Gather>
\t<Redirect>{action_url}</Redirect>
</Response>"""


def build_say_record_xml(language, say_text, record_callback_url,
                         status_callback_url=None, timeout=5, max_length=30,
                         no_input_text=None, redirect_url=None):
	"""
	Build provider-specific XML for a say-then-record pattern.

	Returns XML string. The redirect_url defaults to record_callback_url
	(handles the case where caller doesn't speak and Record times out).
	"""
	provider = get_provider()
	redirect = redirect_url or record_callback_url
	no_input = no_input_text or ""

	if provider == "Exotel":
		return _build_exoml_say_record(
			say_text, record_callback_url, timeout, max_length, no_input, redirect
		)
	return _build_twiml_say_record(
		language, say_text, record_callback_url, status_callback_url,
		timeout, max_length, no_input, redirect
	)


def _build_twiml_say_record(language, say_text, callback_url, status_callback_url,
                             timeout, max_length, no_input_text, redirect_url):
	status_cb = ""
	if status_callback_url:
		status_cb = f' recordingStatusCallback="{status_callback_url}" recordingStatusCallbackMethod="POST"'

	no_input_line = ""
	if no_input_text:
		no_input_line = f'\n\t<Say language="{language}">{no_input_text}</Say>'

	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Say language="{language}">{say_text}</Say>
\t<Record action="{callback_url}"{status_cb} timeout="{timeout}" maxLength="{max_length}" playBeep="false" />{no_input_line}
\t<Redirect>{redirect_url}</Redirect>
</Response>"""


def _build_exoml_say_record(say_text, callback_url, timeout, max_length,
                             no_input_text, redirect_url):
	no_input_line = ""
	if no_input_text:
		no_input_line = f'\n\t<Say>{no_input_text}</Say>'

	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Say>{say_text}</Say>
\t<Record action="{callback_url}" timeout="{timeout}" maxLength="{max_length}" />
\t{no_input_line}
\t<Redirect>{redirect_url}</Redirect>
</Response>"""


def build_goodbye_xml(language, goodbye_text):
	"""Build provider-specific XML for goodbye + hangup."""
	provider = get_provider()
	if provider == "Exotel":
		return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Say>{goodbye_text}</Say>
\t<Hangup/>
</Response>"""

	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Say language="{language}">{goodbye_text}</Say>
\t<Hangup/>
</Response>"""


def build_greeting_record_xml(language, greeting_text, question_text,
                               record_callback_url, status_callback_url=None,
                               timeout=5, max_length=30,
                               no_input_text=None, redirect_url=None):
	"""
	Build XML for greeting + pause + question + record (used for first question).
	"""
	provider = get_provider()
	redirect = redirect_url or record_callback_url
	no_input = no_input_text or ""

	if provider == "Exotel":
		no_input_line = f'\n\t<Say>{no_input}</Say>' if no_input else ""
		return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Say>{greeting_text}</Say>
\t<Say>{question_text}</Say>
\t<Record action="{record_callback_url}" timeout="{timeout}" maxLength="{max_length}" />{no_input_line}
\t<Redirect>{redirect}</Redirect>
</Response>"""

	status_cb = ""
	if status_callback_url:
		status_cb = f' recordingStatusCallback="{status_callback_url}" recordingStatusCallbackMethod="POST"'

	no_input_line = f'\n\t<Say language="{language}">{no_input}</Say>' if no_input else ""

	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
\t<Say language="{language}">{greeting_text}</Say>
\t<Pause length="1"/>
\t<Say language="{language}">{question_text}</Say>
\t<Record action="{record_callback_url}"{status_cb} timeout="{timeout}" maxLength="{max_length}" playBeep="false" />{no_input_line}
\t<Redirect>{redirect}</Redirect>
</Response>"""


def build_error_xml():
	"""Build provider-specific error XML."""
	provider = get_provider()
	if provider == "Exotel":
		return '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Something went wrong. Please try again later.</Say><Hangup/></Response>'
	return '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="hi-IN">Kuch galat ho gaya. Kripya baad mein try karein.</Say><Hangup/></Response>'


# ---------------------------------------------------------------------------
# Recording download
# ---------------------------------------------------------------------------

def download_recording(recording_url):
	"""
	Download a call recording with provider-appropriate auth.

	Returns audio bytes, or None on failure.
	"""
	provider = get_provider()

	if provider == "Exotel":
		return _download_exotel_recording(recording_url)
	return _download_twilio_recording(recording_url)


def _download_twilio_recording(recording_url):
	"""Download from Twilio with HTTP Basic Auth."""
	auth = None
	if "api.twilio.com" in recording_url:
		from requests.auth import HTTPBasicAuth
		settings = frappe.get_single("Twilio Settings")
		auth = HTTPBasicAuth(
			settings.account_sid,
			settings.get_password("auth_token"),
		)

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
					title="Voice Ops: Recording Download Failed",
					message=f"Failed to download from {recording_url}: {e}",
				)
				return None


def _download_exotel_recording(recording_url):
	"""Download from Exotel (typically S3 URLs, no auth needed)."""
	for attempt in range(3):
		try:
			response = requests.get(recording_url, timeout=60)
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
					title="Voice Ops: Recording Download Failed",
					message=f"Failed to download from {recording_url}: {e}",
				)
				return None


# ---------------------------------------------------------------------------
# Recording attachment
# ---------------------------------------------------------------------------

def download_and_attach_recording(call_log_name, recording_url, audio_bytes=None):
	"""
	Attach recording as a File to the call log document.

	Works with both Twilio Call Log and Call Log doctypes.
	"""
	audio_content = audio_bytes or download_recording(recording_url)
	if not audio_content:
		return None

	ext = ".mp3"
	if ".wav" in recording_url:
		ext = ".wav"

	call_log_dt = get_call_log_doctype()
	file_name = f"call_recording_{call_log_name}{ext}"

	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"content": audio_content,
		"is_private": 1,
		"attached_to_doctype": call_log_dt,
		"attached_to_name": call_log_name,
	})
	file_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return file_doc.name


# ---------------------------------------------------------------------------
# WhatsApp messaging (via Twilio Messages API)
# ---------------------------------------------------------------------------

def send_whatsapp(to_phone, message_body):
	"""
	Send a WhatsApp message via Twilio Messages API.

	Args:
		to_phone: Recipient phone number with country code (e.g. +919876543210)
		message_body: Message text

	Returns:
		Message SID on success, None on failure.
	"""
	settings = frappe.get_single("Voice Ops Settings")
	if not settings.enable_whatsapp_notifications:
		return None

	whatsapp_from = settings.twilio_whatsapp_number
	if not whatsapp_from:
		frappe.log_error(
			"Twilio WhatsApp number not configured in Voice Ops Settings",
			"Voice Ops: WhatsApp Send Failed",
		)
		return None

	# Use Twilio REST API directly (works regardless of telephony_provider)
	twilio_settings = frappe.get_single("Twilio Settings")
	account_sid = twilio_settings.account_sid
	auth_token = twilio_settings.get_password("auth_token")

	url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

	# Ensure whatsapp: prefix
	if not whatsapp_from.startswith("whatsapp:"):
		whatsapp_from = f"whatsapp:{whatsapp_from}"
	to_whatsapp = f"whatsapp:{to_phone}" if not to_phone.startswith("whatsapp:") else to_phone

	try:
		response = requests.post(
			url,
			data={
				"From": whatsapp_from,
				"To": to_whatsapp,
				"Body": message_body,
			},
			auth=(account_sid, auth_token),
			timeout=30,
		)
		response.raise_for_status()
		return response.json().get("sid")
	except requests.exceptions.RequestException as e:
		frappe.log_error(
			title="Voice Ops: WhatsApp Send Failed",
			message=f"Failed to send WhatsApp to {to_phone}: {e}",
		)
		return None
