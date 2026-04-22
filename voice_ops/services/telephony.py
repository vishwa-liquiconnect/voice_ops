"""
Telephony Provider Abstraction

Provides a unified interface for Twilio and Exotel telephony operations:
call initiation, XML response building, recording download, and call log management.

Providers are configured independently per flow in Voice Ops Settings:
- outbound_telephony_provider: for checklist calls to drivers
- inbound_telephony_provider: for driver query calls
"""

import frappe
import requests
from frappe.utils import get_url


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def get_provider(flow="outbound"):
	"""
	Return the configured telephony provider for a given flow.

	Args:
		flow: 'outbound' (checklist calls) or 'inbound' (driver queries)

	Returns:
		'Twilio' or 'Exotel'
	"""
	if flow == "inbound":
		return frappe.db.get_single_value("Voice Ops Settings", "inbound_telephony_provider") or "Twilio"
	return frappe.db.get_single_value("Voice Ops Settings", "outbound_telephony_provider") or "Twilio"


def get_call_log_doctype(flow="outbound"):
	"""Return the Call Log doctype name for the given flow's provider."""
	if get_provider(flow) == "Exotel":
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

def initiate_call(to_number, twiml_url, reference_doctype=None, reference_name=None, flow="outbound"):
	"""
	Initiate an outbound call via the configured provider for the given flow.

	Args:
		to_number: Recipient phone number
		twiml_url: URL the provider should fetch for call flow instructions
		reference_doctype: DocType to link in call log
		reference_name: Document name to link
		flow: 'outbound' or 'inbound'

	Returns:
		Call log document name (str)
	"""
	provider = get_provider(flow)
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


def _initiate_exotel_call(to_number, callback_url, reference_doctype, reference_name,
                           flow_app_id=None, type_of_call=None, custom_field=None):
	"""
	Initiate call via Exotel API with a dynamic flow URL or a static App flow.

	Args:
		flow_app_id: Override the outbound Exotel App ID. If None, reads
			`exotel_flow_app_id` from Voice Ops Settings; if that's empty,
			falls back to dynamic Url passthru.
		type_of_call: Telephony Call Type label to tag on the Call Log
			(e.g. "Feedback"). Used downstream to route recording handling.
		custom_field: Raw CustomField value forwarded to Exotel (e.g.
			"checklist_run=CR-001"). Defaults to the checklist_run key when
			a reference_name is passed, preserving legacy behavior.
	"""
	settings = frappe.get_single("Exotel Settings")
	if not settings.enabled:
		frappe.throw("Exotel integration is not enabled.")

	endpoint = (
		f"https://{settings.api_key}:{settings.get_password('api_token')}"
		f"@api.exotel.com/v1/Accounts/{settings.account_sid}/Calls/connect.json?details=true"
	)

	callback_url = force_https(callback_url) if callback_url else None
	exophone = _get_exophone()
	caller_id = frappe.db.get_single_value("Voice Ops Settings", "exotel_caller_id") or exophone
	to_number = _normalize_exotel_number(to_number)
	if flow_app_id is None:
		flow_app_id = (frappe.db.get_single_value("Voice Ops Settings", "exotel_flow_app_id") or "").strip()
	else:
		flow_app_id = (flow_app_id or "").strip()

	payload = {
		"From": exophone,
		"To": to_number,
		"CallerId": caller_id,
		"Record": "true",
	}
	if flow_app_id:
		payload["App"] = flow_app_id
		if custom_field:
			payload["CustomField"] = custom_field
		elif reference_name:
			payload["CustomField"] = f"checklist_run={reference_name}"
	else:
		if not callback_url:
			frappe.throw("Exotel call needs either a flow_app_id or a callback_url.")
		payload["Url"] = callback_url

	response = requests.post(endpoint, data=payload, timeout=30)
	if not response.ok:
		frappe.log_error(
			title="Voice Ops: Exotel Call Initiation Failed",
			message=(
				f"Status: {response.status_code}\n"
				f"Response: {response.text}\n"
				f"From: {exophone} | To: {to_number} | CallerId: {caller_id}\n"
				f"Payload: {payload}"
			),
		)
		frappe.throw(f"Exotel rejected call ({response.status_code}): {response.text}")

	call_data = response.json().get("Call", {})
	call_sid = call_data.get("Sid")

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

	if type_of_call and frappe.db.exists("Telephony Call Type", type_of_call):
		frappe.db.set_value("Call Log", call_log.name, "type_of_call", type_of_call)
		frappe.db.commit()

	return call_log.name


def _normalize_exotel_number(number):
	"""Strip spaces/dashes and ensure Exotel-accepted format (+91... or 0...)."""
	if not number:
		return number
	cleaned = "".join(ch for ch in str(number) if ch.isdigit() or ch == "+")
	if cleaned.startswith("+"):
		return cleaned
	if cleaned.startswith("91") and len(cleaned) == 12:
		return "+" + cleaned
	if cleaned.startswith("0") and len(cleaned) == 11:
		return cleaned
	if len(cleaned) == 10:
		return "+91" + cleaned
	return cleaned


def _get_exophone():
	"""Get the Exotel exophone from Voice Ops Settings."""
	exophone = frappe.db.get_single_value("Voice Ops Settings", "exotel_exophone")
	if not exophone:
		frappe.throw("No Exotel exophone configured in Voice Ops Settings.")
	return exophone


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

def build_gather_xml(prompt_lines, action_url, num_digits=1, timeout=10, provider=None):
	"""
	Build provider-specific XML for DTMF digit collection.

	Args:
		prompt_lines: List of (language, text) tuples to say inside the Gather
		action_url: URL to POST the collected digits to
		num_digits: Number of digits to collect
		timeout: Seconds to wait for input
		provider: 'Twilio' or 'Exotel' (explicit; avoids ambiguity with per-flow settings)
	"""
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
                         no_input_text=None, redirect_url=None, provider=None):
	"""
	Build provider-specific XML for a say-then-record pattern.

	Returns XML string. The redirect_url defaults to record_callback_url
	(handles the case where caller doesn't speak and Record times out).

	Args:
		provider: 'Twilio' or 'Exotel' (explicit; avoids ambiguity with per-flow settings)
	"""
	provider = provider or "Twilio"
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


def build_goodbye_xml(language, goodbye_text, provider=None):
	"""Build provider-specific XML for goodbye + hangup."""
	provider = provider or "Twilio"
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
                               no_input_text=None, redirect_url=None, provider=None):
	"""
	Build XML for greeting + pause + question + record (used for first question).
	"""
	provider = provider or "Twilio"
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


def build_error_xml(provider=None):
	"""Build provider-specific error XML."""
	provider = provider or "Twilio"
	if provider == "Exotel":
		return '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Something went wrong. Please try again later.</Say><Hangup/></Response>'
	return '<?xml version="1.0" encoding="UTF-8"?><Response><Say language="hi-IN">Kuch galat ho gaya. Kripya baad mein try karein.</Say><Hangup/></Response>'


# ---------------------------------------------------------------------------
# Recording download
# ---------------------------------------------------------------------------

def download_recording(recording_url, flow="outbound"):
	"""
	Download a call recording with provider-appropriate auth.

	Args:
		recording_url: URL to download from
		flow: 'outbound' or 'inbound' (determines auth method based on provider)

	Returns audio bytes, or None on failure.
	"""
	provider = get_provider(flow)

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
	"""Download from Exotel with API credentials (basic auth)."""
	auth = None
	if "exotel.com" in recording_url:
		from requests.auth import HTTPBasicAuth
		settings = frappe.get_single("Exotel Settings")
		auth = HTTPBasicAuth(settings.api_key, settings.get_password("api_token"))

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


# ---------------------------------------------------------------------------
# Recording attachment
# ---------------------------------------------------------------------------

def download_and_attach_recording(call_log_name, recording_url, audio_bytes=None, flow="outbound"):
	"""
	Attach recording as a File to the call log document.

	Works with both Twilio Call Log and Call Log doctypes.
	"""
	audio_content = audio_bytes or download_recording(recording_url, flow=flow)
	if not audio_content:
		return None

	ext = ".mp3"
	if ".wav" in recording_url:
		ext = ".wav"

	call_log_dt = get_call_log_doctype(flow)
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


def send_twilio_whatsapp_template(to_phone, content_sid, content_variables=None):
	"""Send a WhatsApp template message via Twilio Messages API using a Content SID.

	The template must be pre-approved via Twilio Content API + Meta review.
	Variable substitution happens via `ContentVariables` (a positional dict
	keyed by "1", "2", ...).

	Args:
		to_phone: Recipient in E.164 format (e.g. +919876543210).
		content_sid: Twilio Content SID (HX...).
		content_variables: Dict of positional variables, e.g.
			{"1": "Vishwa", "2": "ISS-2026-00001"}.

	Returns:
		Twilio Message SID on success, None on failure.
	"""
	import json as _json

	if not to_phone or not content_sid:
		return None

	settings = frappe.get_single("Voice Ops Settings")
	whatsapp_from = (settings.twilio_whatsapp_number or "").strip()
	if not whatsapp_from:
		frappe.log_error(
			"Twilio WhatsApp sender not configured in Voice Ops Settings",
			"Voice Ops: Twilio WhatsApp Template Send Failed",
		)
		return None

	twilio_settings = frappe.get_single("Twilio Settings")
	account_sid = twilio_settings.account_sid
	auth_token = twilio_settings.get_password("auth_token")
	if not (account_sid and auth_token):
		frappe.log_error(
			"Twilio Settings missing account_sid / auth_token",
			"Voice Ops: Twilio WhatsApp Template Send Failed",
		)
		return None

	if not whatsapp_from.startswith("whatsapp:"):
		whatsapp_from = f"whatsapp:{whatsapp_from}"
	to_whatsapp = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"

	payload = {
		"From": whatsapp_from,
		"To": to_whatsapp,
		"ContentSid": content_sid,
	}
	if content_variables:
		payload["ContentVariables"] = _json.dumps({
			str(k): ("" if v is None else str(v))
			for k, v in content_variables.items()
		})

	url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

	try:
		response = requests.post(
			url,
			data=payload,
			auth=(account_sid, auth_token),
			timeout=30,
		)
		response.raise_for_status()
		return response.json().get("sid")
	except requests.exceptions.RequestException as e:
		detail = ""
		try:
			if e.response is not None:
				detail = f"\nResponse: {e.response.status_code} {e.response.text}"
		except Exception:
			pass
		frappe.log_error(
			title="Voice Ops: Twilio WhatsApp Template Send Failed",
			message=f"Failed to send to {to_phone} (content_sid={content_sid}): {e}{detail}",
		)
		return None


def send_exotel_whatsapp(to_phone, template_name, template_params=None, language=None):
	"""
	Send a WhatsApp template message via Exotel's v2 Messages API.

	Exotel requires pre-approved templates for transactional acks. Body
	parameters are passed positionally and map to {{1}}, {{2}}, ... in
	the approved template body.

	Args:
		to_phone: Recipient in E.164 format (e.g. +919876543210).
		template_name: Pre-approved Exotel template name.
		template_params: Iterable of positional body parameters (stringified).
		language: Language code registered on the template (e.g. "en", "hi").
			Falls back to Voice Ops Settings, then "en".

	Returns:
		Exotel message SID on success, None on failure.
	"""
	if not to_phone or not template_name:
		return None

	settings = frappe.get_single("Voice Ops Settings")
	if not settings.enable_exotel_whatsapp_ack:
		return None

	from_number = (settings.exotel_whatsapp_from or "").strip()
	if not from_number:
		frappe.log_error(
			"Exotel WhatsApp sender not configured in Voice Ops Settings",
			"Voice Ops: Exotel WhatsApp Send Failed",
		)
		return None

	lang_code = (language or settings.exotel_whatsapp_template_language or "en").strip()

	exotel_settings = frappe.get_single("Exotel Settings")
	account_sid = exotel_settings.account_sid
	api_key = exotel_settings.api_key
	api_token = exotel_settings.get_password("api_token")

	if not (account_sid and api_key and api_token):
		frappe.log_error(
			"Exotel Settings missing account_sid / api_key / api_token",
			"Voice Ops: Exotel WhatsApp Send Failed",
		)
		return None

	components = []
	params_list = list(template_params or [])
	if params_list:
		components.append({
			"type": "body",
			"parameters": [
				{"type": "text", "text": "" if p is None else str(p)}
				for p in params_list
			],
		})

	payload = {
		"whatsapp": {
			"messages": [{
				"from": from_number,
				"to": to_phone,
				"content": {
					"type": "template",
					"template": {
						"name": template_name,
						"language": {"policy": "deterministic", "code": lang_code},
						"components": components,
					},
				},
			}],
		},
	}

	url = f"https://api.exotel.com/v2/accounts/{account_sid}/messages"

	try:
		response = requests.post(
			url,
			json=payload,
			auth=(api_key, api_token),
			timeout=30,
		)
		response.raise_for_status()
		body = response.json() or {}
		messages = (body.get("whatsapp") or {}).get("messages") or []
		if messages:
			return messages[0].get("sid") or messages[0].get("id")
		return body.get("sid") or body.get("id")
	except requests.exceptions.RequestException as e:
		detail = ""
		try:
			if e.response is not None:
				detail = f"\nResponse: {e.response.status_code} {e.response.text}"
		except Exception:
			pass
		frappe.log_error(
			title="Voice Ops: Exotel WhatsApp Send Failed",
			message=f"Failed to send Exotel WhatsApp to {to_phone} (template={template_name}): {e}{detail}",
		)
		return None
