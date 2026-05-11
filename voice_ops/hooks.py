"""
Frappe app manifest for voice_ops.

Every key in this file is read by Frappe at install / boot time:

- `app_name`, `app_title`, etc. — registration metadata.
- `required_apps` — Frappe refuses to install voice_ops without these.
- `after_install` — runs `voice_ops.setup.after_install` once on install
  to seed default Checklist Templates and required Telephony Call Types.
- `app_include_js` — injected on every desk page; here it loads the
  global proxy that rewrites raw Exotel <audio src> tags so the browser
  doesn't get hit with HTTP Basic Auth prompts when ERPNext's call_link
  timeline template renders a recording.
- `doctype_js` — per-doctype form scripts (only Call Log here; other
  doctypes ship their script alongside their JSON in voice_ops/doctype/).
- `doc_events` — document hooks. Twilio Call Log and Call Log both fan
  out to call_log_handler so we capture the recording and trigger
  downstream processing regardless of which provider ingested the call.
- `scheduler_events` — cron schedule. The every-5-minute list drives
  every auto-trigger / retry / digest job; the daily 3 a.m. entry purges
  old recording files (`recording_retention`).

Touch this file when adding a new doctype with form scripts, a new cron,
or a new document hook. Implementation lives in the modules referenced
by dotted path below.
"""

app_name = "voice_ops"
app_title = "Voice Ops"
app_publisher = "Liqui-Connect"
app_description = "Multilingual AI-driven driver operations platform for intercity bus fleets"
app_email = "admin@lnder.in"
app_license = "mit"

required_apps = ["frappe", "erpnext"]

after_install = "voice_ops.setup.after_install"

# Global Desk JS
# --------------
# Rewrites the raw Exotel <audio src="..."> that ERPNext's call_link
# timeline template injects on any form with linked Call Logs, so the
# browser never hits the Basic Auth challenge.
app_include_js = "/assets/voice_ops/js/timeline_recording_proxy.js"

# Doctype JS
# ----------
doctype_js = {
	"Call Log": "public/js/call_log.js",
}

# Document Events
# ----------------
# Detect call completion and trigger processing for linked Checklist Runs
doc_events = {
	"Twilio Call Log": {
		"on_update": "voice_ops.jobs.call_log_handler.on_twilio_call_log_update",
	},
	"Call Log": {
		"before_validate": "voice_ops.jobs.call_log_handler.fix_exotel_null_status",
		"after_insert": "voice_ops.jobs.call_log_handler.attach_exotel_recording",
		"on_update": [
			"voice_ops.jobs.call_log_handler.attach_exotel_recording",
			"voice_ops.jobs.call_log_handler.on_exotel_call_log_update",
		],
	},
}

# Scheduled Tasks
# ---------------
scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"voice_ops.jobs.auto_trigger.check_and_trigger",
			"voice_ops.jobs.post_trip_trigger.check_and_trigger_post_trip",
			"voice_ops.jobs.feedback_trigger.check_and_trigger",
			"voice_ops.jobs.retry_calls.process_pending_retries",
			"voice_ops.jobs.voicemail_digest.check_and_send",
		],
		"0 3 * * *": [
			"voice_ops.jobs.recording_retention.purge_old_recordings",
		],
	},
}
