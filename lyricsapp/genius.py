"""Genius fallback, for tracks LRCLIB has never heard of.

LRCLIB is community-contributed and thin on recent and underground releases;
Genius covers much of what it misses. No line timings, and it means reading the
song page, so every failure here returns None and lets the caller fall through.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

from .config import LYRICS_SCHEMA_VERSION
from .matching import _artist_matches, _strip_features, _strip_noise, _title_matches
from . import net

# --------------------------------------------------------------------------
# Genius -- fallback for tracks LRCLIB has never heard of
# --------------------------------------------------------------------------

# Genius has no lyrics endpoint, so this reads the song page. That means a
# browser User-Agent (the default one gets a 403) and HTML parsing that will
# eventually break when they redesign -- hence every failure here returns None
# and lets the caller fall through, rather than raising.
_DIV_TAG = re.compile(r"<(/?)div\b[^>]*>", re.I)
_CONTAINER_OPEN = re.compile(r'<div[^>]+data-lyrics-container="true"[^>]*>', re.I)
# Genius's own marker for UI inside a lyrics container -- the "23 Contributors ·
# Translations" header, embedded annotation prompts. Not lyrics.
_EXCLUDED_OPEN = re.compile(r'<div[^>]+data-exclude-from-selection="true"[^>]*>', re.I)


def _div_spans(html_text, open_re):
    """(start, inner_start, inner_end, end) for each <div> matching open_re,
    honouring nested divs.

    A regex can't do this: matching lazily up to the first "</div>" stops at
    the first *nested* div's closing tag. That's how songs came back as a
    7-line fragment of a 36-line page.
    """
    spans = []
    for m in open_re.finditer(html_text):
        depth = 1
        for t in _DIV_TAG.finditer(html_text, m.end()):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                spans.append((m.start(), m.end(), t.start(), t.end()))
                break
    return spans


def _drop_divs(html_text, open_re):
    out, pos = [], 0
    for start, _, _, end in _div_spans(html_text, open_re):
        if start < pos:
            continue  # nested inside one already removed
        out.append(html_text[pos:start])
        pos = end
    out.append(html_text[pos:])
    return "".join(out)


def _lyric_blocks(page):
    return [page[i:j] for _, i, j, _ in _div_spans(page, _CONTAINER_OPEN)]


# Pages open with "5 Contributors\nSwimming Lyrics" before the first real line.
_GENIUS_HEADER = re.compile(r"\A\s*(?:\d+\s+Contributors?.*?\n)?.*?\bLyrics\s*\n",
                            re.S)
_GENIUS_TAIL = re.compile(r"\n?\s*(?:You might also like|\d*\s*Embed)\s*\Z", re.I)


def find_song(track):
    """(artist, title, url) for this track on Genius, or None.

    Same verification as the LRCLIB path: a title-shaped search will otherwise
    hand back a cover or a same-named song by someone else.
    """
    name = _strip_noise(track.get("name") or "")
    artist = (track.get("artist") or "").strip()
    if not name or not artist:
        return None
    query = urllib.parse.urlencode({"q": f"{artist} {name}"})
    try:
        found = json.loads(net._http_text(
            "https://genius.com/api/search/multi?" + query))
    except Exception:
        return None
    for section in found.get("response", {}).get("sections", []):
        if section.get("type") != "song":
            continue
        for h in section.get("hits", []):
            r = h.get("result", {})
            got_artist = (r.get("primary_artist") or {}).get("name", "")
            if (_artist_matches(artist, got_artist)
                    and _title_matches(name, r.get("title") or "")
                    and r.get("url")):
                return got_artist, r.get("title"), r["url"]
    return None


def lyric_html(page):
    """The lyric containers with Genius's own UI removed, still as HTML --
    the formatting is what carries who-sings-which-line."""
    blocks = _lyric_blocks(page)
    if not blocks:
        return None
    return "\n".join(_drop_divs(b, _EXCLUDED_OPEN) for b in blocks)


def fetch_lyrics_genius(track):
    """Last-resort provider. Plain text only -- Genius has no line timings."""
    hit = find_song(track)
    if not hit:
        return None
    got_artist, got_title, url = hit
    try:
        page = net._http_text(url, timeout=25)
    except Exception:
        return None

    text = lyric_html(page)
    if text is None:
        return None  # markup changed; fail quietly rather than serve garbage
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</(?:div|p)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = _GENIUS_HEADER.sub("", text, count=1)
    text = _GENIUS_TAIL.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < 40:
        return None

    return {
        "kind": "plain",
        "lines": [{"t": None, "text": l.strip()} for l in text.split("\n")],
        "plain": text,
        "source": "Genius",
        "lrclib_id": None,
        "matched_artist": got_artist,
        "matched_track": got_title,
        "v": LYRICS_SCHEMA_VERSION,
    }


# --------------------------------------------------------------------------
# Vocal credits -- who sings which line
# --------------------------------------------------------------------------
#
# Genius labels each section with its vocalists, and when a section is shared
# the label doubles as a formatting legend:
#
#     [Verse: Blood Orange, <i>Blood Orange & Caroline Polachek</i>,
#             <b>Caroline Polachek</b>]
#
# Plain lines are the first group, italic lines the second, bold the third.
# Tags often open on one line and close several lines later, so the walk has
# to carry formatting state across line breaks rather than look at each line
# alone.

_FMT_TAG = re.compile(r"<(/?)(i|em|b|strong)\b[^>]*>", re.I)
_GROUP_I = re.compile(r"<(i|em)\b[^>]*>(.*?)</\1>", re.I | re.S)
_GROUP_B = re.compile(r"<(b|strong)\b[^>]*>(.*?)</\1>", re.I | re.S)
_ANY_TAG = re.compile(r"<[^>]+>")


def _visible(fragment):
    return html.unescape(_ANY_TAG.sub("", fragment))


def _split_names(text, keep=()):
    """'A, B & C' -> ['A', 'B', 'C'], without breaking names that contain a
    separator themselves ('Tyler, The Creator', 'Earth, Wind & Fire')."""
    held = {}
    for n, name in enumerate(sorted({k for k in keep if k}, key=len, reverse=True)):
        if name in text:
            token = f"\x00{n}\x00"
            held[token] = name
            text = text.replace(name, token)
    parts = re.split(r"\s*(?:,|&|\+|\band\b)\s*", text)
    out = []
    for part in parts:
        part = part.strip()
        for token, name in held.items():
            part = part.replace(token, name)
        if part and part not in out:
            out.append(part)
    return out


def _parse_label(seg_html, keep=()):
    """'[Verse: A, <i>A & B</i>, <b>B</b>]' -> ('Verse', {'': [A], 'i': [A, B],
    'b': [B]}). A label with no colon ('[Verse 1]') credits nobody."""
    inner = seg_html.strip()
    inner = re.sub(r"^(?:<[^>]+>)*\[", "", inner)
    inner = re.sub(r"\](?:<[^>]+>)*$", "", inner)
    if ":" not in _visible(inner):
        return _visible(inner).strip(), None
    head, _, rest = inner.partition(":")
    groups = {
        "i": [n for m in _GROUP_I.finditer(rest)
              for n in _split_names(_visible(m.group(2)), keep)],
        "b": [n for m in _GROUP_B.finditer(rest)
              for n in _split_names(_visible(m.group(2)), keep)],
    }
    plain = _GROUP_B.sub("", _GROUP_I.sub("", rest))
    groups[""] = _split_names(_visible(plain), keep)
    return _visible(head).strip(), groups


def vocal_lines(lyric_page_html, primary, keep=()):
    """[{'text', 'singers', 'section', 'credited'}] for every sung line.

    `credited` is False where Genius named nobody and the primary artist is
    assumed -- callers shouldn't present that as fact.
    """
    out = []
    state = {"i": 0, "b": 0}
    section, groups = None, None
    for seg in re.split(r"<br\s*/?>", lyric_page_html):
        text = _visible(seg).strip()
        if re.fullmatch(r"\[[^\]]*\]", text):
            section, groups = _parse_label(seg, keep)
            state = {"i": 0, "b": 0}     # a label closes its own tags
            continue

        # How many visible characters sit under each formatting state; the
        # line belongs to whichever covers most of it, so a trailing plain
        # ad-lib doesn't steal a bold line.
        counts = {"": 0, "i": 0, "b": 0}
        pos = 0
        for m in _FMT_TAG.finditer(seg):
            fmt = "b" if state["b"] else ("i" if state["i"] else "")
            counts[fmt] += len(_visible(seg[pos:m.start()]).strip())
            key = "b" if m.group(2).lower() in ("b", "strong") else "i"
            state[key] = max(0, state[key] + (-1 if m.group(1) else 1))
            pos = m.end()
        fmt = "b" if state["b"] else ("i" if state["i"] else "")
        counts[fmt] += len(_visible(seg[pos:]).strip())

        if not text:
            continue
        if groups:
            dominant = max(counts, key=counts.get)
            singers = groups.get(dominant) or groups.get("") or [primary]
            credited = True
        else:
            singers, credited = [primary], False
        out.append({"text": text, "singers": singers, "section": section,
                    "credited": credited})
    return out
