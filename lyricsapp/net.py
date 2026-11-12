"""Raw HTTP helpers shared by the lyric providers."""

from __future__ import annotations

import json
import urllib.request

from .config import USER_AGENT

# Genius serves a 403 to the default urllib User-Agent, so its page fetches go
# out looking like a browser.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36")



def _http_json(url, headers=None, data=None, timeout=15):
    # `data=b""` still means POST -- an empty body is how LRCLIB's challenge
    # endpoint is called, so test `is not None` rather than truthiness.
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data is not None else "GET")
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))



def _http_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


