"""Who sings which line, lined up against the lyrics on screen.

Genius credits vocalists per section and, for shared sections, per line. The
lyrics on screen usually come from LRCLIB instead, whose line breaks and
spellings differ, so the credits are aligned onto those lines rather than
trusted by position.

Without this the analysis has to guess who is singing, and it guesses from the
artist billing: a duet billed to a male-named project gets read as one man
narrating the whole song.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from . import genius, net
from .matching import _norm

# Below this share of on-screen lines matching a Genius line, the page is
# probably a different song with the same title, or a different version.
# Mislabelling who sings is worse than not labelling at all.
MIN_COVERAGE = 0.5

def _feature_names(track_name):
    m = re.search(r"[\(\[]\s*(?:feat\.?|ft\.?|with)\s+([^\)\]]+)[\)\]]",
                  track_name or "", re.I)
    if not m:
        return []
    return [n.strip() for n in re.split(r"\s*(?:,|&|\band\b)\s*", m.group(1)) if n.strip()]


def _close(a, b):
    """Same line, allowing for small spelling differences, and for one source
    splitting a line the other keeps whole."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 8 and (a in b or b in a):
        return True
    return len(a) > 8 and SequenceMatcher(None, a, b).ratio() > 0.85


def align(display_lines, sung):
    """Per on-screen line, the singers -- or None where unknown.

    Returns (per_line, coverage). The two sources rarely line up one-to-one:
    LRCLIB splits lines Genius keeps whole, and writes out repeats Genius
    prints once. So each on-screen line takes the Genius line it resembles that
    sits *nearest* the current position -- the same one again for the second
    half of a split line, an earlier one for a repeat -- and the position only
    ever moves forward. A strict one-to-one alignment matched 61% of a song
    whose lines were 86% present.

    Lines matching nothing (short ad-libs, mostly) inherit the previous line's
    singers, since they almost always sit inside the section already found.
    """
    genius_n = [_norm(v["text"]) for v in sung]
    per_line = [None] * len(display_lines)
    pos = 0
    matched = total = 0
    last = None
    for i, line in enumerate(display_lines):
        t = _norm(line.get("text") or "")
        if not t:
            continue
        total += 1
        cands = [y for y, g in enumerate(genius_n) if _close(t, g)]
        if cands:
            # nearest to where we are; on a tie, prefer moving forward
            y = min(cands, key=lambda y: (abs(y - pos), y < pos))
            last = sung[y]["singers"]
            if y >= pos - 1:
                pos = y + 1
            matched += 1
        per_line[i] = last
    return per_line, (matched / total if total else 0.0)


def get_vocals(track, lyrics):
    """{'lines': [...per on-screen line...], 'singers': [...], 'credited': bool}
    or None if Genius has nothing usable for this exact song."""
    lines = (lyrics or {}).get("lines") or []
    if not lines or lyrics.get("kind") == "instrumental":
        return None
    hit = genius.find_song(track)
    if not hit:
        return None
    try:
        page = net._http_text(hit[2], timeout=25)
    except Exception:
        return None
    body = genius.lyric_html(page)
    if body is None:
        return None

    keep = [track.get("artist") or ""] + _feature_names(track.get("name"))
    sung = genius.vocal_lines(body, track.get("artist") or "", keep)
    if not sung:
        return None
    per_line, coverage = align(lines, sung)
    if coverage < MIN_COVERAGE:
        return None

    singers = []
    for group in per_line:
        for name in group or []:
            if name not in singers:
                singers.append(name)
    return {
        "lines": per_line,
        "singers": singers,
        # False when Genius never named anyone, so every line is just the
        # billed artist by assumption -- not worth showing as a credit.
        "credited": any(v["credited"] for v in sung),
        "coverage": round(coverage, 2),
    }


def annotate(lyrics, vocals):
    """The lyrics as Claude sees them: a [Name] heading wherever the singer
    changes. Plain text when there's nothing credited to add."""
    lines = (lyrics or {}).get("lines") or []
    if not vocals or not vocals.get("credited"):
        return (lyrics or {}).get("plain") or "\n".join(l.get("text", "") for l in lines)
    out, current = [], None
    for line, who in zip(lines, vocals["lines"]):
        text = line.get("text") or ""
        if who and text.strip() and who != current:
            out.append(f"[{' & '.join(who)}]")
            current = who
        out.append(text)
    return "\n".join(out)
