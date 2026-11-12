"""Spotify Live Lyrics.

Watches the Spotify desktop app, finds time-synced lyrics, and asks Claude what
the song is actually about.

Layout:

    config.py    paths, constants, config.json
    spotify.py   reading now-playing via AppleScript
    net.py       raw HTTP helpers
    matching.py  deciding whether a search hit is really this song
    lyrics.py    LRCLIB lookup, LRC parse/build
    genius.py    fallback provider for tracks LRCLIB lacks
    sync.py      tap-along timing, and publishing it back
    analysis.py  the Claude calls (lyric reading, and web research)
    cache.py     disk cache
    engine.py    the state machine tying it together
    server.py    local web server + JSON API
    page.html    the frontend
    entry.py     CLI

Names are re-exported here for convenience, but note: to *replace* one (in a
test, say) set it on the module that defines it -- `lyricsapp.lyrics.
fetch_lyrics = ...` -- because that's where callers look it up. Rebinding it on
this package only changes this package's copy.
"""

from __future__ import annotations

from http.server import ThreadingHTTPServer  # noqa: F401 (re-export)
from pathlib import Path

from . import (analysis, cache, config, engine, genius, lyrics, matching, net,
               server, spotify, sync, vocals)
from .analysis import (ANALYSIS_PROMPT, ANALYSIS_SCHEMA, CONTEXT_SCHEMA,
                       SYSTEM_PROMPT, AnalysisTransient, AnalysisUnavailable,
                       _AUTH_HINT, _looks_degenerate, _looks_degenerate_context,
                       analyze_song, analyze_song_context, sdk_available,
                       TRANSLATE_SCHEMA, translate_lyrics)
from .cache import cache_read, cache_write
from .config import (ANALYSIS_CACHE, APP_NAME, APP_VERSION, CACHE_DIR,
                     DEFAULT_MODEL, LYRICS_CACHE, LYRICS_SCHEMA_VERSION,
                     POLL_INTERVAL, USER_AGENT, load_config)
from .engine import ANALYSIS_ATTEMPTS, MAX_TRACKS, Engine
from .genius import fetch_lyrics_genius
from .lyrics import _normalize_lyrics, fetch_lyrics, parse_lrc
from .matching import (_artist_matches, _norm, _strip_features,
                       _strip_noise, _title_matches)
from .server import make_handler
from .spotify import APPLESCRIPT, SEP, read_spotify, spotify_installed
from .sync import apply_sync, build_lrc, publish_to_lrclib

# The frontend is 650 lines of HTML/CSS/JS. It lives in its own file rather
# than as a Python string so an editor can actually syntax-highlight it.
PAGE_HTML = (Path(__file__).resolve().parent / "page.html").read_text("utf-8")

__version__ = APP_VERSION
