// Voicemail Digest Log form script.
//
// Surfaces two desk buttons on a saved digest that hasn't been sent yet:
//
//   - "Generate" – calls the server-side `generate` method, which scans
//     Call Logs in the configured window, builds the entries table,
//     asks Claude for an overall summary, and renders the HTML body.
//     Safe to run repeatedly; each call rebuilds the entries.
//
//   - "Send Now" – ships the rendered body to the configured email /
//     WhatsApp recipients. Shown as primary because that's the action
//     ops normally cares about. Runs `generate()` first if the doc is
//     still in Draft, so a fresh digest can be sent without the
//     two-click round-trip. Wrapped in `frappe.confirm` because the
//     send is not reversible.
//
// Sent digests intentionally hide both buttons so the same digest can't
// be sent twice by accident — the scheduler creates a fresh log per
// window via `jobs/voicemail_digest.py`.

frappe.ui.form.on("Voicemail Digest Log", {
	refresh(frm) {
		// New (unsaved) docs have no name yet, so server calls would fail.
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
