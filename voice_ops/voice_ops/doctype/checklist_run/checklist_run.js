frappe.ui.form.on("Checklist Run", {
	refresh(frm) {
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(
				__("Trigger Call"),
				function () {
					frappe.call({
						method: "voice_ops.api.checklist.trigger_checklist_call",
						args: { checklist_run_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Initiating call..."),
						callback: function (r) {
							if (r.message) {
								frappe.show_alert({
									message: __("Call initiated successfully"),
									indicator: "green",
								});
								frm.reload_doc();
							}
						},
					});
				},
				null,
				"primary"
			);
		}
	},
});
