frappe.ui.form.on("Call Log", {
	refresh(frm) {
		if (frm.doc.recording_url) {
			render_proxied_player(frm);

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

function render_proxied_player(frm) {
	const proxy_url =
		"/api/method/voice_ops.api.call_log.stream_recording?call_log=" +
		encodeURIComponent(frm.doc.name);

	const html = `
		<div class="frappe-control" style="padding: 8px 0;">
			<label class="control-label" style="padding-right: 0px;">${__("Recording")}</label>
			<audio controls preload="none" style="width: 100%; margin-top: 8px;">
				<source src="${proxy_url}" type="audio/mpeg">
				${__("Your browser does not support the audio element.")}
			</audio>
			<div style="margin-top: 4px;">
				<a href="${proxy_url}" download class="text-muted small">${__("Download")}</a>
			</div>
		</div>
	`;

	const wrapper = frm.get_field("recording_url")?.$wrapper;
	if (wrapper) {
		wrapper.find(".voice-ops-player").remove();
		wrapper.append(`<div class="voice-ops-player">${html}</div>`);
	}
}
