#!/usr/bin/env python3
"""
Spotify Live Lyrics + Song Meaning
==================================

Watches whatever is playing in the Spotify desktop app on your Mac, pulls
time-synced lyrics from LRCLIB (falling back to Genius), asks Claude what the
song is actually about, and serves the whole thing as a live-updating page in
your browser.

Requires: macOS, the Spotify desktop app, Python 3.9+.

Song breakdowns run through your existing Claude Code install -- no API key and
no separate billing. They need, once, in this folder:

    python3 -m venv .venv && .venv/bin/pip install claude-agent-sdk
    claude          # and sign in

Lyrics work without either; only the breakdowns are skipped.

Usage:
    python3 spotify_lyrics.py

Then open http://127.0.0.1:8420 (it opens automatically).

The code lives in ./lyricsapp/ -- this file is just the way in.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lyricsapp.entry import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
