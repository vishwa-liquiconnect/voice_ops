app_name = "voice_ops"
app_title = "Voice Ops"
app_publisher = "Liqui-Connect"
app_description = "Multilingual AI-driven driver operations platform for intercity bus fleets"
app_email = "admin@lnder.in"
app_license = "mit"

required_apps = ["frappe", "erpnext", "exotel_integration"]

# Document Events
# ----------------
# Hook into Call Log to detect call completion and trigger processing
doc_events = {
	"Call Log": {
		"on_update": "voice_ops.jobs.call_log_handler.on_call_log_update",
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
