frappe.ui.form.on("Call Log", {
	refresh(frm) {
		if (frm.doc.recording_url) {
			// Wait a tick so ERPNext's setup_recording_audio_control runs first,
			// then we replace its player with the proxied version.
			setTimeout(() => replace_native_player(frm), 50);

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

function replace_native_player(frm) {
	if (!frm.doc.recording_url) return;

	const proxy_url =
		"/api/method/voice_ops.api.call_log.stream_recording?call_log=" +
		encodeURIComponent(frm.doc.name);

	const recording_field = frm.get_field("recording_html");
	if (!recording_field) return;

	const wrapper = recording_field.$wrapper;

	// Abort any in-flight audio load from ERPNext's native player before the
	// browser has a chance to challenge us for Basic Auth.
	wrapper.find("audio").each(function () {
		try {
			this.pause();
			this.removeAttribute("src");
			this.load();
		} catch (e) {
			// ignore
		}
	});

	wrapper.empty().addClass("input-max-width").html(`
		<audio controls preload="none" style="width: 100%;">
			<source src="${proxy_url}" type="audio/mpeg">
			${__("Your browser does not support the audio element.")}
		</audio>
		<div style="margin-top: 4px;">
			<a href="${proxy_url}" download class="text-muted small">${__("Download recording")}</a>
		</div>
	`);
}
