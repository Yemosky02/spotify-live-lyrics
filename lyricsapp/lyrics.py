"""LRCLIB lookup, artist/title verification, and LRC parsing.

Every candidate from a search is checked against the artist *and* the title
before it's trusted: LRCLIB's search will happily return a different act's song
with the same name, which is how "Heartbreaker" by Terry Presume once resolved
to Pat Benatar's.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import genius
from .config import LYRICS_SCHEMA_VERSION
from .matching import (_artist_matches, _strip_features, _strip_noise,
                       _title_matches)
from . import net

# --------------------------------------------------------------------------
# LRCLIB -- synced lyrics
# --------------------------------------------------------------------------



# How far a search hit's runtime may be from Spotify's and still count as the
# same recording for timing purposes.
SYNC_WINDOW = 15
# How far it may be and still count as the same *song* -- right words, timing
# from a different edit (single vs album cut, radio edit). Past this it's
# likely an extended mix or a live take with different structure.
TEXT_WINDOW = 60


def _title_variants(name):
    """Spellings to try, most specific first, deduplicated.

    Spotify puts feature credits in the title -- "Mind Loaded (feat. Caroline
    Polachek, Lorde & Mustafa)" -- and LRCLIB mostly indexes the bare title, so
    a search that only ever sends the full Spotify string misses songs LRCLIB
    has, synced, and falls through to a lower-quality source.
    """
    out = []
    for v in (name, _strip_noise(name), _strip_features(_strip_noise(name))):
        v = v.strip()
        if v and v not in out:
            out.append(v)
    return out


def _with_delta(normalized, raw, duration):
    if normalized is not None and raw.get("duration"):
        normalized["duration_delta"] = round(raw["duration"] - duration)
    return normalized


def _as_plain(raw):
    """Keep the words, drop timing that belongs to a different edit."""
    plain = raw.get("plainLyrics")
    if not plain and raw.get("syncedLyrics"):
        plain = "\n".join(l["text"] for l in parse_lrc(raw["syncedLyrics"]))
    if not plain:
        return None
    out = _normalize_lyrics({**raw, "syncedLyrics": None, "plainLyrics": plain})
    if out is not None:
        out["timing_dropped"] = True
    return out


def fetch_lyrics(track):
    """Try hard to find lyrics for a track. Returns a dict or None."""
    name = track["name"]
    artist = track["artist"]
    album = track["album"]
    duration = max(1, round(track["duration_ms"] / 1000))
    titles = _title_variants(name)
    clean_album = _strip_noise(album)

    # /api/get is an exact lookup; LRCLIB itself enforces the duration match.
    for i, tn in enumerate(titles):
        params = {"track_name": tn, "artist_name": artist,
                  "album_name": album if i == 0 else clean_album,
                  "duration": duration}
        url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
        try:
            raw = net._http_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue   # this exact spelling isn't indexed; try the next
            # 5xx/429 on one spelling says nothing about the others -- this is
            # exactly the case where a 503 used to end the lookup early.
            continue
        except Exception:
            continue
        normalized = _with_delta(_normalize_lyrics(raw), raw, duration)
        if normalized is not None:
            return normalized

    # Search is fuzzy: it will return a different artist's song with the same
    # title, so every hit is checked against the artist before it's trusted.
    # Without that, "Heartbreaker" by Terry Presume resolves to Pat Benatar's.
    same_song_other_edit = []
    queries = [(t, artist) for t in titles] + [(titles[-1], "")]
    for tn, an in queries:
        params = {"track_name": tn}
        if an:
            params["artist_name"] = an
        url = "https://lrclib.net/api/search?" + urllib.parse.urlencode(params)
        try:
            results = net._http_json(url)
        except Exception:
            continue
        if not results:
            continue

        mine = [r for r in results if _artist_matches(artist, r.get("artistName"))]
        timed = [r for r in mine
                 if abs((r.get("duration") or 0) - duration) <= SYNC_WINDOW]
        if timed:
            # Among genuine matches, prefer synced lyrics, then closest runtime.
            best = min(timed, key=lambda r: (0 if r.get("syncedLyrics") else 1,
                                             abs((r.get("duration") or 0) - duration)))
            normalized = _with_delta(_normalize_lyrics(best), best, duration)
            if normalized is not None:
                return normalized

        # Outside the sync window the runtime no longer vouches for the match,
        # so the title has to as well.
        same_song_other_edit += [
            r for r in mine
            if abs((r.get("duration") or 0) - duration) <= TEXT_WINDOW
            and _title_matches(_strip_noise(name), _strip_noise(r.get("trackName") or ""))
        ]

    # The right song, timed for a different edit. The words are still LRCLIB's
    # complete, community-checked text, which beats a scraped Genius page --
    # shown unsynced, since the timestamps belong to another recording.
    if same_song_other_edit:
        best = min(same_song_other_edit,
                   key=lambda r: abs((r.get("duration") or 0) - duration))
        normalized = _with_delta(_as_plain(best), best, duration)
        if normalized is not None:
            return normalized

    # LRCLIB is community-contributed and thin on newer or underground
    # releases. Genius covers a lot of what it misses, at the cost of no line
    # timings -- so it's only consulted once LRCLIB has come up empty.
    return genius.fetch_lyrics_genius(track)


def is_stale(entry):
    """Should a cached lyrics entry be refetched rather than trusted?"""
    if entry is None:
        return False
    # Never throw away timing the user tapped in by hand.
    if entry.get("synced_locally"):
        return False
    v = entry.get("v") or 0
    if v < 2:
        return True   # written before artist verification existed
    if v < 3 and entry.get("source") == "Genius":
        return True   # v2 Genius extraction truncated at nested markup
    return False


def _normalize_lyrics(raw):
    meta = {"source": "LRCLIB", "lrclib_id": raw.get("id"),
            "matched_artist": raw.get("artistName"),
            "matched_track": raw.get("trackName"),
            "v": LYRICS_SCHEMA_VERSION}
    synced = raw.get("syncedLyrics")
    plain = raw.get("plainLyrics")
    if raw.get("instrumental"):
        return {"kind": "instrumental", "lines": [], "plain": "", **meta}
    if synced:
        lines = parse_lrc(synced)
        if lines:
            return {"kind": "synced", "lines": lines,
                    "plain": plain or "\n".join(l["text"] for l in lines), **meta}
    if plain:
        lines = [{"t": None, "text": l.strip()} for l in plain.splitlines()]
        return {"kind": "plain", "lines": lines, "plain": plain, **meta}
    return None


LRC_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text):
    """Parse an LRC blob into [{'t': seconds, 'text': str}, ...] sorted by time."""
    out = []
    for raw_line in text.splitlines():
        stamps = list(LRC_TIME_RE.finditer(raw_line))
        if not stamps:
            continue
        body = raw_line[stamps[-1].end():].strip()
        for m in stamps:
            mins, secs, frac = m.group(1), m.group(2), m.group(3) or "0"
            # frac may be centiseconds (2 digits) or milliseconds (3 digits)
            denom = 10 ** len(frac)
            t = int(mins) * 60 + int(secs) + int(frac) / denom
            out.append({"t": round(t, 3), "text": body})
    out.sort(key=lambda x: x["t"])
    return out


