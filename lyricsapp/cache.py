"""Disk cache for lyrics and analyses."""

from __future__ import annotations

import json
import re

# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def _cache_path(base, key):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:180]
    return base / f"{safe}.json"


def cache_read(base, key):
    p = _cache_path(base, key)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            return None
    return None


def cache_write(base, key, value):
    base.mkdir(parents=True, exist_ok=True)
    try:
        _cache_path(base, key).write_text(json.dumps(value), "utf-8")
    except Exception:
        pass


