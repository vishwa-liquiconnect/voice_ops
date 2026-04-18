// Intercept any <audio> the ERPNext call_link timeline template renders
// with a raw Exotel URL, and swap it to our stream_recording proxy so
// the browser never sees the upstream Basic Auth prompt.
//
// Runs on every desk page (registered via app_include_js). Uses a
// MutationObserver so it handles cards inserted lazily after the
// initial form render.

(function () {
	const UPSTREAM_HOST = "recordings.exotel.com";
	const PROXY_BASE = "/api/method/voice_ops.api.call_log.stream_recording";

	function rewrite(audio) {
		const src = audio.getAttribute("src") || "";
		if (!src.includes(UPSTREAM_HOST)) return;

		// Cancel any in-flight load before replacing src — otherwise the
		// browser has already issued the Basic Auth challenge.
		try { audio.pause(); } catch (_e) {}
		audio.removeAttribute("src");
		try { audio.load(); } catch (_e) {}

		const card = audio.closest(".call-detail-wrapper");
		let name = null;
		if (card) {
			const link = card.querySelector('a[href*="/app/call-log/"]');
			if (link) {
				const match = link.getAttribute("href").match(/\/app\/call-log\/([^\/?#]+)/);
				if (match) name = decodeURIComponent(match[1]);
			}
		}

		audio.setAttribute("preload", "none");
		if (name) {
			audio.setAttribute("src", `${PROXY_BASE}?call_log=${encodeURIComponent(name)}`);
		}
		// If we can't identify the Call Log, leave src cleared — the
		// player becomes a no-op but no auth popup fires.
	}

	function scan(root) {
		(root || document).querySelectorAll(".call-detail-wrapper audio").forEach(rewrite);
	}

	function start() {
		scan(document.body);
		new MutationObserver((mutations) => {
			for (const m of mutations) {
				m.addedNodes.forEach((node) => {
					if (node.nodeType !== 1) return;
					if (node.matches && node.matches(".call-detail-wrapper audio")) {
						rewrite(node);
					}
					if (node.querySelectorAll) scan(node);
				});
			}
		}).observe(document.body, { childList: true, subtree: true });
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", start);
	} else {
		start();
	}
})();
