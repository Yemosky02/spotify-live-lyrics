"""The local web server and its JSON API."""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler

# --------------------------------------------------------------------------
# Web server
# --------------------------------------------------------------------------


FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<circle cx="16" cy="16" r="15" fill="#1ed760"/>'
    '<path d="M9 12.5q7-2 14 1M9.5 17q6-1.6 12 .9M10 21q5-1.2 10 .8" '
    'stroke="#0a0a0c" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>'
)


def make_handler(engine, page_html):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # keep the terminal clean

        def _send(self, code, body, ctype):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                return self._send(200, page_html, "text/html; charset=utf-8")
            if parsed.path == "/favicon.ico":
                return self._send(200, FAVICON, "image/svg+xml")
            if parsed.path == "/api/now":
                return self._send(200, json.dumps(engine.now()), "application/json")
            if parsed.path == "/api/track":
                qs = urllib.parse.parse_qs(parsed.query)
                tid = (qs.get("id") or [""])[0]
                return self._send(200, json.dumps(engine.track_detail(tid)),
                                  "application/json")
            return self._send(404, "not found", "text/plain")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in ("/api/sync", "/api/publish", "/api/translate"):
                return self._send(404, "not found", "text/plain")

            # The server binds 127.0.0.1, so this is local-only -- but the body
            # is still parsed defensively rather than trusted.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 2_000_000:
                return self._send(400, json.dumps({"ok": False,
                                                   "error": "bad request body"}),
                                  "application/json")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                tid = str(payload["id"])
            except Exception:
                return self._send(400, json.dumps({"ok": False,
                                                   "error": "malformed JSON"}),
                                  "application/json")

            if parsed.path == "/api/sync":
                stamps = payload.get("stamps")
                if not isinstance(stamps, list):
                    result = {"ok": False, "error": "stamps must be a list"}
                else:
                    result = engine.save_sync(tid, stamps)
            elif parsed.path == "/api/translate":
                result = engine.translate(tid)
            else:
                # Publishing reaches the network and does ~17M hashes first, so
                # it only ever runs from a deliberate press, never on save.
                result = engine.publish_sync(tid)
            return self._send(200 if result.get("ok") else 400,
                              json.dumps(result), "application/json")

    return Handler


