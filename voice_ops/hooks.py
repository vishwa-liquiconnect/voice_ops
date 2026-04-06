app_name = "voice_ops"
app_title = "Voice Ops"
app_publisher = "Liqui-Connect"
app_description = "Multilingual AI-driven driver operations platform for intercity bus fleets"
app_email = "admin@lnder.in"
app_license = "mit"

required_apps = ["frappe", "erpnext", "twilio_integration"]

after_install = "voice_ops.setup.after_install"

# Document Events
# ----------------
# When Twilio call ends, check if linked Checklist Run needs processing
doc_events = {
	"Twilio Call Log": {
		"on_update": "voice_ops.jobs.call_log_handler.on_twilio_call_log_update",
	},
}

# Scheduled Tasks
# ---------------
scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"voice_ops.jobs.auto_trigger.check_and_trigger",
			"voice_ops.jobs.post_trip_trigger.check_and_trigger_post_trip",
			"voice_ops.jobs.retry_calls.process_pending_retries",
		],
	},
}
