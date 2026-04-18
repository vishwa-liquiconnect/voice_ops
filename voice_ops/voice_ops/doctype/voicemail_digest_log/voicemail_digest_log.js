frappe.ui.form.on("Voicemail Digest Log", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status !== "Sent") {
			frm.add_custom_button(__("Generate"), () => {
				frm.call("generate").then(() => frm.reload_doc());
			});

			frm.add_custom_button(__("Send Now"), () => {
				frappe.confirm(
					__("Send digest {0} to the configured recipients?", [frm.doc.name]),
					() => frm.call("send_now").then(() => frm.reload_doc()),
				);
			}).addClass("btn-primary");
		}
	},
});
