# Spotify Live Lyrics + Song Meaning

Watches whatever you're playing in the Spotify desktop app, shows the lyrics
scrolling in time with the music, and tells you what the song is actually about.

Made for exactly your situation: you love a song, you can't catch half the words,
and you have no idea what it's really saying.

---

## What you need

- A Mac
- The **Spotify desktop app** (the web player and phone app can't be read this way)
- Python 3.9 or newer — already on every Mac. Check with `python3 --version`
- **Claude Code**, for the "what's this song about" part

No API key, no console signup, no per-token billing. The song breakdowns run
through the Claude Code you're already logged into.

The lyrics half needs nothing but Python — if you skip the Claude Code setup
below, you still get scrolling synced lyrics, just no breakdowns.

---

## Setup (about 3 minutes)

### 1. Put it somewhere

Copy the whole folder somewhere you'll remember, like `~/spotify-lyrics/`. You
need `spotify_lyrics.py` **and** the `lyricsapp/` directory next to it — the
script is just the way in; the code lives in the package.

### 2. Install the Claude Agent SDK

This is the one thing the script can't do without a package install — it's what
lets the script talk to your local Claude Code.

Run this **in the folder you put the script in**:

```bash
python3 -m venv .venv && .venv/bin/pip install claude-agent-sdk
```

You still start the script the normal way afterwards (`python3 spotify_lyrics.py`);
it finds the `.venv` next to itself automatically.

> **Why not just `pip3 install claude-agent-sdk`?** On a Mac with Homebrew's
> Python that fails with `error: externally-managed-environment` — Homebrew
> won't let you install into the system Python. The `.venv` above sidesteps it
> and keeps everything contained in one folder you can delete later.

### 3. Make sure Claude Code is signed in

```bash
claude
```

If it drops you into a prompt, you're set — quit with `/exit`. If it asks you to
log in, do that first. Sessions expire every so often, so if breakdowns stop
working later with a message about authentication, this is the fix.

### 4. Run it

```bash
python3 ~/spotify-lyrics/spotify_lyrics.py
```

Your browser opens automatically. Press play in Spotify.

### 5. Say yes to the permission popup

The first time, macOS asks whether Terminal can control Spotify. Click **OK**.

If you miss it or click the wrong thing, fix it at
**System Settings → Privacy & Security → Automation → Terminal → Spotify**.

---

## Using it

Just play music. The page follows along on its own.

- The current line is bright white and stays centred; lines you've passed dim out
- The right-hand panel fills in with what the song is about, its genre, themes,
  how it unfolds, specific lines worth catching, and background on the artist
- For songs with no lyrics anywhere, the panel switches to research mode:
  who the artist is, the scene they come from, and what's known about the release
- On songs with more than one singer, a small name tag marks each change of
  voice, and a **Singing** chip under the title follows the highlighted line
- Scroll the lyrics yourself to read ahead — it stops auto-following for 4 seconds,
  then picks back up

**Keyboard shortcuts** (click the page first):

| Key | Does |
| --- | --- |
| `[` | Lyrics running ahead? Nudge them 0.25s later |
| `]` | Lyrics running behind? Nudge them 0.25s earlier |
| `0` | Reset the timing |
| `s` | Song showing lyrics but not highlighting? Tap along to add the timing |
| `p` | After syncing, send your timing to LRCLIB |
| `t` | Song not in English? Show a translation under each line |

---

## Who's singing

A song billed to one artist is often sung by several — and the person telling
the story isn't necessarily the artist on the cover. The app reads the vocal
credits Genius publishes for each section (and, where a section is shared, for
each line) and lines them up with the lyrics on screen.

Two things come out of that. On the page, each change of voice gets a coloured
name tag, and the chip under the title tells you who is singing *right now*.
And the song breakdown is written knowing who sings what, instead of assuming
one narrator from the artist billing. It's also told never to guess anyone's
gender from a name.

If Genius has no credits for a song, or its copy of the lyrics doesn't match
the one on screen closely enough to be sure, nothing is labelled — a missing
tag is better than a wrong one. Solo songs show no tags at all.

Song breakdowns written before this existed are redone the next time you play
each song, which costs one fresh analysis per song.

---

## Translation

Click **Translate** in the top right (or press **`t`**) and a translation
appears under each line, in the same place the words are. The button becomes
**Hide translation** / **Show translation** once it's fetched, so toggling it
back on is instant. It only appears when there are lyrics to translate.

Only the lines that need it are translated — plenty of songs mix languages line
by line, and repeating back the English ones just adds noise. Where a line turns
on slang or a regional reference, a short note explains the term rather than
silently smoothing it over.

**Lines the translation isn't sure about are dimmed and marked `uncertain`.**
That's the point: dialect, ad-libs and coined words are exactly where a
confident-sounding translation misleads you, so those get flagged instead of
being presented at the same weight as the rest. On a Nigerian Pidgin track with
Yoruba in it, a typical result is around 40 lines confident, a dozen partial,
and a handful flagged — the flagged ones being ad-libs and area slang.

The translation searches the web for terms it doesn't know rather than guessing,
and it's cached per song, so it's a one-time wait.

---

## Adding timing to a song yourself

Roughly a third of tracks arrive as plain text: LRCLIB has the words, but nobody
has timed them, so nothing highlights. You can fix that in one listen.

Press **`s`** and the first line lights up. Tap **space** as each line starts —
it stamps the current playback position and moves to the next. **Backspace**
undoes the last tap, **enter** saves early, **escape** throws it away.

When you finish, the timing is saved locally and the song starts highlighting
immediately. Your taps are pulled back a quarter-second automatically, since
people tap just after hearing a line begin.

Then, if you want, press **`p`** to send that timing to LRCLIB so the next
person gets it for free. That's a separate, deliberate press — nothing is ever
uploaded just because you synced a song. Publishing takes a few seconds:
LRCLIB requires a small proof-of-work to keep spam out, and the script solves it
before sending.

Worth knowing before you press `p`: publishing lyrics timing to a public
database is a redistribution of that song's lyrics. That's what LRCLIB is and
thousands of people contribute to it, but it's your call, which is why it's
never automatic.

Stop the script with `Ctrl+C` in Terminal.

---

## Good to know

**Each song is only analysed once.** Results are cached in
`~/.spotify-live-lyrics/`, so replaying a song is instant. Only genuinely new
songs draw on your Claude Code usage.

**Lyrics come from [LRCLIB](https://lrclib.net) first**, a free community
database — it's the only one of the two with *time-synced* lyrics, which is what
makes the highlighting work. When a song has lyrics there but nobody has synced
them, you get the full text without highlighting.

**[Genius](https://genius.com) is the backstop.** LRCLIB is thin on newer and
underground releases, so when it comes up empty the script checks Genius, which
covers a lot of what LRCLIB misses. Those lyrics are plain text only — no line
timing — and the panel shows a **via Genius** badge so you know why a song isn't
highlighting. Every match from either source is checked against the artist *and*
the title before it's used, so you shouldn't get another act's song with the same
name.

**When a song has no lyrics anywhere, the panel researches it instead.** Rather
than a blank sidebar, you get who the artist is, where they're from, what scene
and genre they sit in, and what's documented about that specific release. This
also covers instrumentals. The panel is labelled so you always know you're
reading background rather than a reading of the words.

This matters most for small artists, which is exactly where a model is tempted
to invent — so this path searches the web before answering and reports how much
it could actually verify. When there's genuinely nothing published, it says so
instead of making something up.

Some tracks genuinely aren't in either lyrics database. Anyone can add lyrics at
lrclib.net if you want to fix that for a song you love.

**It reads the desktop app only.** If you're playing through your phone or a
speaker with the Mac app just acting as a remote, the page will tell you so.
Switch playback to the Mac and it picks up immediately.

**Nothing leaves your machine except**: the song title/artist (to LRCLIB, and to
Genius only if LRCLIB has nothing) and the lyrics (to Claude, to write the
explanation). No account login, no Spotify password, no listening history.

**A note on Genius**: it has no lyrics API, so the script reads the song page the
way a browser would. That's fine for personal use at one request per song —
results are cached, so each song is fetched once ever — but it is against
Genius's terms of service, and it will break whenever they change their page
layout. If that bothers you, delete `fetch_lyrics_genius` and the one call to it
in `fetch_lyrics`; everything else keeps working with LRCLIB alone.

**The breakdown runs with no tools.** Lyrics are third-party text from a public
database, so the Claude call is configured with no filesystem access, no shell,
and none of your local Claude Code settings or `CLAUDE.md`. It can write an
answer and nothing else.

---

## Options

```bash
python3 spotify_lyrics.py --port 9000      # use a different port
python3 spotify_lyrics.py --no-browser     # don't auto-open the browser
python3 spotify_lyrics.py --model claude-haiku-4-5   # faster, shallower read
```

**On the model.** The default is `claude-sonnet-5`, chosen for depth: it's
noticeably better at slang, regional references, and the cultural background
that makes a song land differently once you know it. The cost is waiting — about
57 seconds for a breakdown, against roughly 24 on `claude-haiku-4-5`. The lyrics
scroll immediately either way; it's only the side panel you're waiting on.

If the wait bothers you more than the depth, use `--model claude-haiku-4-5`, or
put `"model": "claude-haiku-4-5"` in `config.json` to make it stick.

Each analysis is cached per model, so switching models re-analyses songs you've
already played, and switching back finds the old one still there.

You can also set `"model"` in `config.json` to make a choice stick. The script
looks for that file in `~/.spotify-live-lyrics/` first, then next to the script
itself:

```json
{
  "model": "claude-haiku-4-5"
}
```

---

## If something's wrong

**"macOS is blocking automation access"** — grant it under System Settings →
Privacy & Security → Automation, then restart the script. Note the permission is
tied to the app you *launched from*, so if you run it in iTerm or VS Code, it's
that app you need to allow, not Terminal.

**"Can't find the Spotify app"** — the script looks in `/Applications` and
`~/Applications`. Install the desktop app from spotify.com.

**Lyrics are slightly out of sync** — press `[` or `]`. Your offset is remembered.

**"No lyrics found"** for a song you'd expect — LRCLIB is contributed by
volunteers, so gaps happen. Anyone can add lyrics at lrclib.net.

**Port already in use** — run with `--port 9000` or any other number.

**The sidebar asks you to install the Claude Agent SDK** — the lyrics work but
the SDK isn't there yet. Run the venv command from step 2 *in the same folder as
`spotify_lyrics.py`*, then restart the script.

**`error: externally-managed-environment`** — you ran `pip3 install` directly.
Use the venv command in step 2 instead.

**"Claude Code isn't logged in"** — your session expired. Run `claude` in a
terminal, sign in, then skip to the next song and back.

---

## How the code is laid out

`spotify_lyrics.py` is a 37-line entry point. Everything else is in `lyricsapp/`:

| File | What's in it |
| --- | --- |
| `config.py` | paths, constants, `config.json` |
| `spotify.py` | reading now-playing via AppleScript |
| `net.py` | the two raw HTTP helpers |
| `matching.py` | deciding whether a search hit is really this song |
| `lyrics.py` | LRCLIB lookup, LRC parse/build |
| `genius.py` | fallback provider |
| `sync.py` | tap-along timing and publishing it back |
| `analysis.py` | the Claude calls |
| `cache.py` | disk cache |
| `engine.py` | the state machine tying it together |
| `server.py` | local web server + JSON API |
| `page.html` | the frontend — 713 lines that used to be a Python string |

One thing to know if you're editing: to *replace* a function in a test, set it on
the module that defines it (`lyricsapp.lyrics.fetch_lyrics = ...`), not on the
package. Callers look names up on their defining module, so rebinding
`lyricsapp.fetch_lyrics` only changes the package's own copy and the real code
never sees it.

---

## Want to double-check it yourself

`test_harness.py` and `test_ui.py` are included. They mock out Spotify, LRCLIB,
and Claude, so the whole thing can be exercised without the network, a Spotify
install, or a Claude Code login. `test_ui.py` additionally drives the real page
in a headless browser and verifies the lyric highlighting, auto-scroll, and every
error screen.

```bash
python3 test_harness.py
python3 test_ui.py        # needs: pip3 install playwright
```
