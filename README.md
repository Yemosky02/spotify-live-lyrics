# Spotify Live Lyrics

**Time-synced lyrics for whatever's playing in Spotify, with Claude explaining what the song is actually about.**

You love a song, you can't catch half the words, and you have no idea what it's
really saying. This watches the Spotify desktop app on your Mac, scrolls the
lyrics in your browser in time with the music, and fills a side panel with a
breakdown of the song: what it's about, its themes and genre, who's singing
which part, the lines worth catching, and the slang or references you might
have missed.

> macOS only. Reads the Spotify **desktop** app — not the web player or your
> phone.

---

## Features

- **Live, synced lyrics.** The current line is highlighted and kept centred as
  the song plays. Scroll to read ahead; it picks back up after a few seconds.
- **Song breakdowns.** A summary, themes, mood, genre, how the song unfolds,
  key lines decoded, and background on the artist — written by Claude through
  your existing [Claude Code](https://claude.com/claude-code) install. No API
  key needed.
- **Who's singing.** On songs with more than one vocalist, each change of voice
  is labelled, a chip under the title follows whoever is singing right now, and
  the breakdown is written knowing which singer says what.
- **Translation.** One click puts an English translation under each line of a
  non-English song. Lines the translation isn't sure about are marked
  *uncertain* rather than stated with false confidence.
- **Research mode.** When a song has no lyrics anywhere (very new releases,
  instrumentals, small artists), the panel looks the song and artist up on the
  web instead of going blank — and says so when there's genuinely little
  published, rather than inventing it.
- **Tap-to-sync.** About a third of songs only have untimed lyrics. Tap along
  once and they highlight from then on — and you can contribute that timing
  back to [LRCLIB](https://lrclib.net) for everyone.
- **Careful matching.** Every lyrics match is checked against both artist and
  title, so you don't get a different act's song that happens to share a name.

---

## How it works

```
Spotify desktop app ──AppleScript──▶  what's playing, and where in the song
                                        │
                     LRCLIB (synced) ───┤──▶ Genius (fallback, untimed)
                                        │
             Genius vocal credits ──────┤   who sings each line
                                        │
        Claude, via Claude Code ────────┤   breakdown / research / translation
                                        ▼
                  local web page at http://127.0.0.1:8420
```

Everything runs on your machine. Results are cached in `~/.spotify-live-lyrics/`,
so each song is only looked up and analysed once.

---

## Requirements

- macOS
- The [Spotify desktop app](https://www.spotify.com/download/)
- Python 3.9+ (`python3 --version`)
- [Claude Code](https://claude.com/claude-code), signed in — only for the
  breakdown panel. Without it you still get scrolling synced lyrics.

---

## Install

```bash
git clone https://github.com/Yemosky02/spotify-live-lyrics.git
cd spotify-live-lyrics
python3 -m venv .venv && .venv/bin/pip install claude-agent-sdk
```

The virtualenv holds the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk),
which is how the script talks to your local Claude Code. You don't need to
activate it — the script finds `.venv` next to itself and uses it.

> **Why a venv?** On a Mac with Homebrew's Python, a plain
> `pip3 install claude-agent-sdk` fails with `error: externally-managed-environment`.

Make sure Claude Code is signed in:

```bash
claude      # sign in if prompted, then /exit
```

## Run

```bash
python3 spotify_lyrics.py
```

Your browser opens to `http://127.0.0.1:8420`. Press play in Spotify.

The first time, macOS asks whether your terminal can control Spotify — click
**OK**. If you miss it, enable it under **System Settings → Privacy & Security →
Automation**. The permission belongs to the app you launched from (Terminal,
iTerm, VS Code…), so that's the one to allow.

---

## Using it

Click the page first so it receives key presses.

| Control | Does |
| --- | --- |
| **Translate** button, or `t` | Show / hide a translation under each line |
| `[` / `]` | Lyrics ahead or behind? Nudge the timing by 0.25s (remembered) |
| `0` | Reset the timing nudge |
| `s` | Tap-to-sync a song whose lyrics aren't timed |
| `p` | After syncing, publish your timing to LRCLIB |

### Tap-to-sync

Press `s` and the first line lights up. Tap **space** as each line starts;
**backspace** undoes, **enter** saves early, **escape** cancels. Your taps are
pulled back a quarter-second automatically, since people tap just after hearing
a line begin. The timing is saved locally and starts highlighting immediately.

Publishing to LRCLIB with `p` is a separate, deliberate step — nothing is ever
uploaded just because you synced a song. Bear in mind that publishing lyrics to a
public database redistributes them; that's how LRCLIB works, but it's your call.

### Badges you might see

- **via Genius** — LRCLIB had nothing, so the words came from Genius, without timing.
- **timing approximate** — the timing was made for a slightly different edit of
  the song (single vs album cut). Use `[` / `]` if it drifts.
- **different edit — unsynced** — LRCLIB only has timing for a version too
  different to use, so you get its words without highlighting.

---

## Configuration

```bash
python3 spotify_lyrics.py --port 9000                # different port
python3 spotify_lyrics.py --no-browser               # don't open a browser
python3 spotify_lyrics.py --model claude-haiku-4-5   # faster, shallower breakdowns
```

The default model is `claude-sonnet-5`, chosen for depth — it's noticeably better
at slang, regional references and cultural background. A breakdown takes about a
minute on Sonnet and under half that on Haiku. The lyrics scroll immediately
either way; only the side panel waits.

To make a setting stick, create `config.json` in `~/.spotify-live-lyrics/` or
next to `spotify_lyrics.py`:

```json
{ "model": "claude-haiku-4-5" }
```

Analyses are cached per model, so switching models re-analyses songs, and
switching back finds the earlier ones still there.

---

## Privacy

**What leaves your machine:** the song title and artist (to LRCLIB, and to Genius
when LRCLIB has nothing or to look up vocal credits), and the lyrics (to Claude,
for the breakdown and translation). No Spotify login, no password, no listening
history.

**Claude runs sandboxed.** Lyrics are third-party text from a public database,
so the breakdown and translation calls get no shell, no file access, and none of
your local Claude Code settings or `CLAUDE.md`. Research mode and translation may
search the web, and nothing beyond that.

---

## A note on lyrics sources

- **[LRCLIB](https://lrclib.net)** is a free, community-run database and the only
  source here with time-synced lyrics. It's tried first, always.
- **[Genius](https://genius.com)** fills gaps and provides vocal credits. It has
  no lyrics API, so the script reads the song page as a browser would. Results
  are cached so each page is fetched once, but this is against Genius's terms of
  service and will break if they change their page layout. To use LRCLIB alone,
  remove `fetch_lyrics_genius` and its call in `lyricsapp/lyrics.py`, and the
  `get_vocals` call in `lyricsapp/engine.py`.
- Some songs aren't in any database yet — especially recent releases, which
  volunteers haven't caught up with. Anyone can add them at lrclib.net.

---

## Troubleshooting

| You see | Fix |
| --- | --- |
| "macOS is blocking automation access" | Allow your terminal app under **Privacy & Security → Automation**, then restart. |
| "Can't find the Spotify app" | Install the desktop app into `/Applications`. |
| "Playing on another device" | Switch playback to this Mac — the page picks it up immediately. |
| "Claude Code isn't logged in" | Run `claude` and sign in, then skip to the next song and back. No restart needed. |
| Sidebar asks you to install the Claude Agent SDK | Run the venv command from **Install**, in this folder, then restart. |
| `error: externally-managed-environment` | Use the venv command from **Install** instead of `pip3 install`. |
| Lyrics slightly out of sync | Press `[` or `]`. |
| Port already in use | `--port 9000` |

---

## Development

```
spotify_lyrics.py      entry point
lyricsapp/
  config.py            paths, constants, config.json
  spotify.py           reading now-playing via AppleScript
  net.py               raw HTTP helpers
  matching.py          deciding whether a search hit is really this song
  lyrics.py            LRCLIB lookup, LRC parsing
  genius.py            Genius fallback and vocal-credit parsing
  vocals.py            aligning vocal credits with the lyrics on screen
  sync.py              tap-to-sync and publishing to LRCLIB
  analysis.py          the Claude calls
  cache.py             disk cache
  engine.py            the state machine tying it together
  server.py            local web server and JSON API
  page.html            the frontend
```

The tests mock Spotify, the lyrics sources and Claude, so they run without a
network connection, a Spotify install or a Claude login:

```bash
python3 test_harness.py                                    # core behaviour
.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium
.venv/bin/python test_ui.py                                # drives the real page in a headless browser
```

When replacing a function in a test, set it on the module that **defines** it
(`lyricsapp.lyrics.fetch_lyrics = ...`), not on the `lyricsapp` package — callers
look names up on their defining module, so rebinding the package's copy has no
effect.

---

## Disclaimer

An unofficial personal project. Not affiliated with or endorsed by Spotify,
Genius, LRCLIB or Anthropic. Lyrics belong to their respective rights holders;
this project stores no lyrics in its repository and fetches them only for your
own listening.

No license has been chosen yet, so by default all rights are reserved.
