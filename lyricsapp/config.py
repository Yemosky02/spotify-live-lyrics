"""Paths, constants, and the config file.

Cache paths live here as module attributes rather than being imported by value
elsewhere -- other modules read `config.LYRICS_CACHE` at call time so tests (and
anything else) can repoint them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_NAME = "SpotifyLiveLyrics"
APP_VERSION = "2.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION} (https://lrclib.net)"

CACHE_DIR = Path.home() / ".spotify-live-lyrics"
LYRICS_CACHE = CACHE_DIR / "lyrics"
ANALYSIS_CACHE = CACHE_DIR / "analysis"

# Sonnet, chosen for depth over speed: it reads slang, regional references, and
# cultural background far better than Haiku, which is the whole point of the
# breakdown panel. The cost is wait time -- measured on a 1.9KB lyric sheet,
# ~57s here against ~24s on claude-haiku-4-5 (pass --model to switch).
#
# If you come back to tune that wait: the time is model generation, roughly
# 5000 output tokens of prose. It is NOT SDK or CLI startup (first message
# lands in ~0.6s), and lowering `output_config.effort` measured *slower* than
# the default, not faster. Trimming the schema bought only ~4s. The model is
# the only lever that moved this.
DEFAULT_MODEL = "claude-sonnet-5"

POLL_INTERVAL = 1.0  # seconds between AppleScript polls

# Bumped whenever a bug could have written a wrong entry to disk. Which older
# versions get refetched is decided per entry by lyrics.is_stale(), so a bump
# never discards timing the user tapped in by hand.
#   2: artist verification on every search hit
#   3: Genius extraction no longer truncates at nested markup
LYRICS_SCHEMA_VERSION = 3


def load_config():
    """Read config.json, preferring ~/.spotify-live-lyrics/ but also accepting
    one sitting next to the script -- which is where people naturally put it,
    and which the old code silently ignored."""
    candidates = [CACHE_DIR / "config.json",
                  Path(__file__).resolve().parent.parent / "config.json"]
    for cfg_path in candidates:
        try:
            text = cfg_path.read_text("utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        try:
            cfg = json.loads(text)
        except ValueError as ex:
            print(f"! Ignoring malformed {cfg_path}: {ex}", file=sys.stderr)
            continue
        if isinstance(cfg, dict):
            return cfg
    return {}
