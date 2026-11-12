"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import PAGE_HTML
from .analysis import sdk_available
from .config import CACHE_DIR, DEFAULT_MODEL, load_config
from .engine import Engine
from .server import make_handler
from .spotify import spotify_installed

# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def reexec_into_venv():
    """Re-run under ./.venv/bin/python3 if that's where the SDK lives.

    Homebrew's Python refuses system-wide `pip install` (PEP 668), so the SDK
    realistically ends up in a venv next to the script. Without this, running
    the obvious `python3 spotify_lyrics.py` would silently skip every breakdown.
    """
    if os.environ.get("SLL_NO_REEXEC") or sdk_available():
        return
    root = Path(__file__).resolve().parent.parent
    venv_dir = root / ".venv"
    venv_py = venv_dir / "bin" / "python3"
    if not venv_py.exists():
        return
    # Are we already inside that venv? Compare sys.prefix, not the executable
    # path: .venv/bin/python3 is a symlink back to the system interpreter, so
    # resolving it makes the venv look identical to the interpreter we're on.
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except OSError:
        return
    # Sentinel: if the venv also lacks the SDK, the child must not bounce back.
    os.environ["SLL_NO_REEXEC"] = "1"
    print(f"  Using {venv_py}")
    try:
        os.execv(str(venv_py), [str(venv_py), str(root / "spotify_lyrics.py"),
                                *sys.argv[1:]])
    except OSError as ex:
        print(f"! Couldn't switch to {venv_py}: {ex}", file=sys.stderr)


def main():
    reexec_into_venv()
    ap = argparse.ArgumentParser(description="Live Spotify lyrics + song meaning.")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--model", default=None, help=f"Claude model (default {DEFAULT_MODEL})")
    ap.add_argument("--no-browser", action="store_true", help="Don't auto-open the page")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("This script reads the Spotify desktop app via AppleScript, so it "
              "needs macOS.", file=sys.stderr)
        return 1

    if not spotify_installed():
        print("! Couldn't find Spotify.app in /Applications. Install the Spotify "
              "desktop app -- the web player can't be read this way.\n")

    cfg = load_config()
    model = args.model or cfg.get("model") or DEFAULT_MODEL

    if not sdk_available():
        here = Path(__file__).resolve().parent.parent
        print("! claude-agent-sdk isn't installed -- lyrics will work, song "
              "breakdowns will be skipped.")
        print("  Install it once, into a venv beside this script:")
        print(f"      cd {here}")
        print("      python3 -m venv .venv && .venv/bin/pip install claude-agent-sdk")
        print("  Then run this script the same way as always -- it picks the "
              "venv up on its own.")
        print("  (Breakdowns use your Claude Code login; there's no API key to "
              "set up.)")
        print()

    engine = Engine(model)
    threading.Thread(target=engine.poll_forever, daemon=True).start()

    handler = make_handler(engine, PAGE_HTML)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"  Spotify Live Lyrics running at {url}")
    print(f"  Model: {model}   Cache: {CACHE_DIR}")
    print("  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    return 0


