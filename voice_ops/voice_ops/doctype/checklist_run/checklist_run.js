// Checklist Run form script.
//
// Adds a "Trigger Call" button on the Checklist Run form when the run
// is saved but hasn't been dialled yet (status === "Draft"). The button
// hits the whitelisted API in `voice_ops.api.checklist`, which places
// an outbound call via the configured telephony provider (Twilio or
// Exotel) and updates the run's status.
//
// Auto-triggered runs (from jobs/auto_trigger.py) reach the same
// endpoint without involving this UI — this button is for manual /
// re-trigger flows from the desk.

frappe.ui.form.on("Checklist Run", {
	refresh(frm) {
		// Only show the button on saved Draft runs. New (unsaved) docs
		// have no name, so the server call would fail; non-Draft runs
		// are already in flight or completed.
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(
				__("Trigger Call"),
				function () {
					frappe.call({
						method: "voice_ops.api.checklist.trigger_checklist_call",
						args: { checklist_run_name: frm.doc.name },
						// Freeze the UI while the call is being placed —
						// the provider API can take a couple of seconds.
						freeze: true,
						freeze_message: __("Initiating call..."),
						callback: function (r) {
							if (r.message) {
								frappe.show_alert({
									message: __("Call initiated successfully"),
									indicator: "green",
								});
								// Reload so the status (now "Call Initiated")
								// and any call_log link become visible.
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
