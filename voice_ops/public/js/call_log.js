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
							frm.set_value("summary", r.message);
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
