"""Reading what Spotify is playing, via AppleScript."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Field delimiter for the AppleScript payload. Deliberately a plain-ASCII
# multi-character sequence: control characters inside AppleScript string
# literals are fragile, and no real track title contains this.
SEP = "<|@|>"


# --------------------------------------------------------------------------
# Spotify (via AppleScript)
# --------------------------------------------------------------------------

APPLESCRIPT = f'''
if application "Spotify" is running then
    tell application "Spotify"
        set pState to (player state as text)
        if pState is "stopped" then return "STOPPED"

        -- `player position` is a real, in SECONDS. Emit it as integer
        -- milliseconds via `div` so the output can't come back with a comma
        -- decimal separator (some locales) or in E-notation.
        try
            set pPosMs to ((player position) * 1000) div 1
        on error
            set pPosMs to -1
        end try

        -- `current track` can fail even while the player reports playing --
        -- most commonly when playback is being driven through Spotify Connect
        -- on another device, but also for some podcast/local-file items.
        try
            set t to current track
            set tId to (id of t)
            set tName to (name of t)
            set tArtist to (artist of t)
            set tAlbum to (album of t)
            set tDur to (duration of t)  -- MILLISECONDS (the sdef says seconds; it lies)
        on error errMsg number errNum
            return "NOTRACK" & "{SEP}" & pState & "{SEP}" & errNum
        end try

        if tId is missing value then set tId to ""
        if tName is missing value then set tName to ""
        if tArtist is missing value then set tArtist to ""
        if tAlbum is missing value then set tAlbum to ""
        if tDur is missing value then set tDur to 0

        set tArt to ""
        try
            set artURL to (artwork url of t)
            if artURL is not missing value then set tArt to artURL
        end try

        return "OK" & "{SEP}" & pState & "{SEP}" & pPosMs & "{SEP}" & tId & "{SEP}" & tName & "{SEP}" & tArtist & "{SEP}" & tAlbum & "{SEP}" & tDur & "{SEP}" & tArt
    end tell
else
    return "NOTRUNNING"
end if
'''

PERMISSION_HELP = (
    "macOS is blocking automation access. Open System Settings > Privacy & "
    "Security > Automation, find the app you launched this from (Terminal, "
    "iTerm, VS Code...), and switch on Spotify. Then restart the script."
)


def spotify_installed():
    return any(Path(p).exists() for p in (
        "/Applications/Spotify.app",
        str(Path.home() / "Applications/Spotify.app"),
    ))


def read_spotify():
    """Return a dict describing what Spotify is doing right now."""
    if not spotify_installed():
        return {"status": "not_installed"}
    try:
        proc = subprocess.run(
            ["osascript", "-e", APPLESCRIPT],
            capture_output=True, text=True, timeout=8,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Spotify didn't respond in time."}
    except FileNotFoundError:
        return {"status": "error", "message": "Couldn't find osascript. Is this macOS?"}

    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or "").strip()
        # -1743: user denied automation. -1744: consent can't be requested here
        # (e.g. launched from ssh or a launchd agent).
        if "-1743" in err or "-1744" in err or "not authorized" in err.lower():
            return {"status": "error", "message": PERMISSION_HELP}
        if "-600" in err or "-609" in err:
            return {"status": "not_running"}
        return {"status": "error", "message": err or "No response from Spotify."}

    if out == "NOTRUNNING":
        return {"status": "not_running"}
    if out == "STOPPED":
        return {"status": "stopped"}

    parts = out.split(SEP)

    if parts[0] == "NOTRACK":
        return {"status": "no_track"}

    # Exactly 9 fields. Fewer means a truncated payload; more means a metadata
    # field contained the delimiter, which would silently shift every field
    # after it -- wrong artist, wrong duration, wrong lyrics.
    if len(parts) != 9 or parts[0] != "OK":
        return {"status": "error",
                "message": f"Unexpected Spotify response: {out[:200]}"}

    _, state, pos_ms, tid, name, artist, album, dur, art = parts

    def _num(v, default=0.0):
        try:
            return float(str(v).strip().replace(",", "."))
        except (TypeError, ValueError):
            return default

    # Some builds hand back http:// artwork URLs.
    if art.startswith("http://"):
        art = "https://" + art[len("http://"):]
    # The page drops this straight into a CSS url(), so anything that isn't a
    # plain https URL gets thrown away rather than escaped.
    if art and not re.fullmatch(r"https://[^\s\"'()\\]+", art):
        art = ""

    position = _num(pos_ms, -1000.0) / 1000.0
    if position < 0:
        position = 0.0

    return {
        "status": "ok",
        "playing": state.strip().lower() == "playing",
        "position": position,
        "track": {
            "id": tid,
            "name": name,
            "artist": artist,
            "album": album,
            "duration_ms": int(_num(dur)),
            "artwork_url": art,
        },
    }


