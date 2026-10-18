"""Load the card the way Lovelace does, as a strict ES module from an HTTP URL, in a real headless browser, and
report: console errors, whether the element is defined, whether setConfig accepts the Watch config, and how many
tiles render from the sample payload. Local only: a throwaway http.server on 127.0.0.1 and Playwright.

    python tests/module_check.py
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import render_card as rc  # noqa: E402


def main() -> int:
    data = rc.sample_payload()
    tmp = tempfile.mkdtemp(prefix="pp-module-")
    shutil.copy(rc.CARD, os.path.join(tmp, "plex-picks-card.js"))
    html = f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="out"></div><plex-picks-card id="card"></plex-picks-card>
<script>
window.__errors = [];
window.addEventListener('error', e => window.__errors.push('ERROR ' + e.message + ' @' + e.lineno));
window.addEventListener('unhandledrejection', e => window.__errors.push('REJECTION ' + (e.reason && e.reason.message || e.reason)));
customElements.define('home-assistant', class extends HTMLElement {{}});
const DATA = {json.dumps(data)};
window.hass = {{ language: 'en', states: {{}}, callWS: async (m) => DATA, callService: async () => ({{}}), connection: {{ subscribeEvents: async () => () => {{}} }} }};
</script>
<script type="module" src="/plex-picks-card.js?v=test"></script>
</body></html>"""
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), lambda *a, **k: Quiet(*a, directory=tmp, **k))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:  # noqa: BLE001
            browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        console = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_timeout(1500)
        result = page.evaluate("""() => {
            const el = document.getElementById('card');
            const r = { defined: !!customElements.get('plex-picks-card'), customCards: (window.customCards || []).map(c => c.type), errors: window.__errors };
            try { el.setConfig({ rows: ['continue', 'because', 'picked', 'again', 'recent'], players: ['media_player.x'] }); r.setConfig = 'ok'; }
            catch (e) { r.setConfig = 'THREW ' + e.message; }
            el.hass = window.hass;
            return r;
        }""")
        page.wait_for_timeout(1200)
        after = page.evaluate("""() => { const sr = document.getElementById('card').shadowRoot; return { tiles: sr ? sr.querySelectorAll('.tile').length : -1, rows: sr ? sr.querySelectorAll('.row').length : -1, text: sr ? (sr.querySelector('.msg') || {}).textContent : null, errors: window.__errors }; }""")
        browser.close()
    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("module load :", json.dumps(result))
    print("after hass  :", json.dumps(after))
    print("console     :", console or "clean")
    ok = result.get("defined") and result.get("setConfig") == "ok" and after.get("tiles", 0) > 0 and not after.get("errors")
    print("VERDICT     :", "card defines, accepts the config and renders as an ES module" if ok else "PROBLEM, see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
