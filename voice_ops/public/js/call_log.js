frappe.ui.form.on("Call Log", {
	refresh(frm) {
		if (frm.doc.recording_url) {
			frm.add_custom_button(__("Summarize Recording"), function () {
				frappe.call({
					method: "voice_ops.api.call_log.summarize_recording",
					args: { call_log_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Transcribing recording..."),
					callback(r) {
						if (r.message) {
							const { transcript, summary } = r.message;
							if (transcript) frm.set_value("transcript", transcript);
							if (summary) frm.set_value("summary", summary);
							frm.save();
							frappe.show_alert({
								message: __("Recording transcribed successfully"),
								indicator: "green",
							});
						}
					},
				});
			});
		}
	},
});
