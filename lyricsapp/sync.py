"""Tap-along timing for lyrics that only exist as plain text."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request

from .config import LYRICS_SCHEMA_VERSION, USER_AGENT
from . import net

# --------------------------------------------------------------------------
# Sync mode -- add timing to lyrics that only exist as plain text
# --------------------------------------------------------------------------

# About a third of tracks come back as plain text: LRCLIB has the words but
# nobody has timed them. Tapping along once fixes that locally, and can be sent
# back so the next person gets highlighting for free.


def build_lrc(lines):
    """[{'t': seconds, 'text': str}, ...] -> an LRC blob."""
    out = []
    for l in lines:
        t = l.get("t")
        if t is None:
            continue
        t = max(0.0, float(t))
        mins, rem = divmod(t, 60)
        out.append(f"[{int(mins):02d}:{rem:05.2f}]{l.get('text', '')}")
    return "\n".join(out)


def apply_sync(lyrics, stamps):
    """Merge user-tapped timestamps into a plain lyrics dict.

    `stamps` is a list of {"i": line index, "t": seconds}. Lines left unstamped
    keep t=None, so a partial pass still works -- the highlighter simply won't
    advance past the last timed line.
    """
    if not lyrics or not lyrics.get("lines"):
        raise ValueError("no lyrics to sync")
    lines = [dict(l) for l in lyrics["lines"]]
    seen = {}
    for s in stamps:
        i, t = int(s["i"]), float(s["t"])
        if not (0 <= i < len(lines)):
            raise ValueError(f"line index {i} out of range")
        if t < 0:
            raise ValueError("negative timestamp")
        seen[i] = t
    if not seen:
        raise ValueError("no timestamps provided")
    # Timestamps must not go backwards, or the highlighter's forward scan
    # silently sticks on whichever line came out of order.
    prev = -1.0
    for i in sorted(seen):
        if seen[i] < prev:
            raise ValueError(f"timestamp for line {i} goes backwards")
        prev = seen[i]
        lines[i]["t"] = round(seen[i], 2)
    synced = dict(lyrics)
    synced["lines"] = lines
    synced["kind"] = "synced"
    synced["source"] = (lyrics.get("source") or "LRCLIB") + " + your timing"
    synced["synced_locally"] = True
    synced["v"] = LYRICS_SCHEMA_VERSION
    return synced


def _solve_challenge(prefix, target_hex, deadline=60.0):
    """LRCLIB gates publishing with a leading-zeros proof of work. ~17M hashes
    on the current target; a couple of seconds."""
    target = bytes.fromhex(target_hex)
    started = time.monotonic()
    nonce = 0
    while True:
        if hashlib.sha256(f"{prefix}{nonce}".encode()).digest() < target:
            return nonce
        nonce += 1
        if nonce % 500_000 == 0 and time.monotonic() - started > deadline:
            raise TimeoutError("proof-of-work took too long")


def publish_to_lrclib(track, lyrics):
    """Send a synced version back to LRCLIB. Never called automatically."""
    synced = build_lrc(lyrics.get("lines") or [])
    if not synced.strip():
        raise ValueError("nothing timed yet")

    challenge = net._http_json("https://lrclib.net/api/request-challenge",
                           data=b"", timeout=20)
    nonce = _solve_challenge(challenge["prefix"], challenge["target"])

    body = json.dumps({
        "trackName": track["name"],
        "artistName": track["artist"],
        "albumName": track.get("album") or "",
        "duration": max(1, round(track["duration_ms"] / 1000)),
        "plainLyrics": lyrics.get("plain") or "",
        "syncedLyrics": synced,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://lrclib.net/api/publish", data=body, method="POST")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Publish-Token", f"{challenge['prefix']}:{nonce}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


