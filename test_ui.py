"""Drive the real page in Chromium against a mocked engine and verify the
lyric highlighting, sidebar rendering, and state transitions."""
import json, os, sys, threading, time, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lyricsapp as S
from test_harness import SAMPLE_LRC, FAKE_ANALYSIS
from playwright.sync_api import sync_playwright

shutil.rmtree("/tmp/uicache", ignore_errors=True)
S.config.CACHE_DIR = S.Path("/tmp/uicache")
S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"

LYRIC_TRACK = {"id": "spotify:track:one", "name": "Bohemian Test - 2011 Remaster",
               "artist": "The Mock Band", "album": "Greatest Mocks",
               "duration_ms": 302000, "artwork_url": ""}
NOLYR_TRACK = {"id": "spotify:track:two", "name": "Unknown B-Side",
               "artist": "Nobody", "album": "Nothing", "duration_ms": 180000,
               "artwork_url": ""}
PLAIN_TRACK = {"id": "spotify:track:three", "name": "Plain Song",
               "artist": "Someone", "album": "An Album", "duration_ms": 200000,
               "artwork_url": ""}

st = {"track": LYRIC_TRACK, "pos": 0.0, "playing": True, "status": "ok"}

# Captures what sync mode would send to LRCLIB, without touching the network.
published = []

S.spotify.read_spotify = lambda: (
    {"status": st["status"], "playing": st["playing"], "position": st["pos"],
     "track": st["track"]} if st["status"] == "ok" else {"status": st["status"]})
# A realistically long song so auto-scroll is actually exercised.
def _stamp(total):
    return f"[{total // 60:02d}:{total % 60:02d}.00]"

LONG_LRC = SAMPLE_LRC + "".join(
    f"{_stamp(155 + i * 3)}Filler lyric line number {i}\n" for i in range(40))

def _fake_fetch(t):
    if t["id"].endswith("one"):
        return S._normalize_lyrics(
            {"id": 9, "instrumental": False, "syncedLyrics": LONG_LRC,
             "plainLyrics": "First line here\nSecond line",
             "artistName": t["artist"], "trackName": t["name"]})
    if t["id"].endswith("three"):
        # Words but no timing -- the case sync mode exists for.
        return S._normalize_lyrics(
            {"id": 31, "instrumental": False, "syncedLyrics": None,
             "plainLyrics": "plain one\nplain two\nplain three\nplain four",
             "artistName": t["artist"], "trackName": t["name"]})
    return None

S.lyrics.fetch_lyrics = _fake_fetch
S.sync.publish_to_lrclib = lambda track, lyrics: (
    published.append(S.build_lrc(lyrics["lines"])), 201)[1]
S.analysis.analyze_song = lambda *a, **k: FAKE_ANALYSIS
S.analysis.translate_lyrics = lambda track, lyrics, model: {
    "source_language": "Nigerian Pidgin",
    "needs_translation": True,
    "lines": [{"i": 2, "en": "There's trouble", "note": "wahala = trouble",
               "confidence": "high"},
              {"i": 3, "en": "an ad-libbed tag", "note": "", "confidence": "low"}],
}
S.analysis.sdk_available = lambda: True
# First three lines sung by a featured singer, the rest by the billed artist.
S.vocals.get_vocals = lambda track, lyrics: (
    {"credited": True, "singers": ["Singer B", "Band A"], "coverage": 1.0,
     "lines": [(["Singer B"] if i < 3 else ["Band A"]) for i in range(len(lyrics["lines"]))]}
    if track["id"].endswith("one") else None)
S.config.POLL_INTERVAL = 0.4

eng = S.Engine("claude-sonnet-5")
threading.Thread(target=eng.poll_forever, daemon=True).start()
srv = S.ThreadingHTTPServer(("127.0.0.1", 8932), S.make_handler(eng, S.PAGE_HTML))
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(1.5)

fails = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(extra)))
    if not cond:
        fails.append(name)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium"
                          if S.Path("/opt/pw-browsers/chromium").exists() else None)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text)
          if m.type == "error" else None)

    pg.goto("http://127.0.0.1:8932/")
    pg.wait_for_timeout(2500)

    check("no JS errors on load", not errors, errors)
    check("title rendered", "Bohemian Test" in pg.inner_text("#title"),
          pg.inner_text("#title"))
    check("artist rendered", "The Mock Band" in pg.inner_text("#artist"))
    n_lines = pg.eval_on_selector_all("#lyrics .line", "els => els.length")
    check("47 lyric lines rendered", n_lines == 47, n_lines)
    check("synced badge shown", "synced" in pg.inner_text("#badges").lower(),
          pg.inner_text("#badges"))
    side = pg.inner_text("#analysis")
    check("sidebar one-liner", FAKE_ANALYSIS["one_liner"] in side)
    check("sidebar themes", "testing" in side and "mocks" in side)
    check("sidebar key line", "It is first." in side)
    check("sidebar background", "Written by a script." in side)
    pg.screenshot(path=os.path.join(HERE, "shot_1_start.png"))

    # ---- advance playback to 16s: line 2 ("Second line", t=15.5) active ----
    st["pos"] = 16.0
    pg.wait_for_timeout(1800)
    active = pg.eval_on_selector_all(
        "#lyrics .line.active", "els => els.map(e => e.textContent)")
    check("exactly one active line", len(active) == 1, active)
    check("correct line active at t=16s", active and active[0] == "Second line", active)
    past = pg.eval_on_selector_all("#lyrics .line.past", "els => els.length")
    check("earlier line marked past", past == 1, past)
    check("singer tags mark each change of voice",
          pg.eval_on_selector_all("#lyrics .who", "e => e.map(x => x.textContent.trim())")
          == ["Singer B", "Band A"],
          pg.eval_on_selector_all("#lyrics .who", "e => e.map(x => x.textContent.trim())"))
    check("chip names who is singing the active line",
          "Singer B" in pg.inner_text("#singer") and pg.is_visible("#singer"),
          pg.inner_text("#singer"))
    pg.screenshot(path=os.path.join(HERE, "shot_2_synced.png"))

    # ---- jump to 151s: "Last one" (t=150.99) ----
    st["pos"] = 151.0
    pg.wait_for_timeout(2000)
    active = pg.eval_on_selector_all("#lyrics .line.active", "els => els.map(e=>e.textContent)")
    check("correct line active at t=151s", active == ["Last one"], active)
    scrolled = pg.eval_on_selector("#lyrics", "e => e.scrollTop")
    check("lyrics auto-scrolled", scrolled > 0, scrolled)

    # ---- deep into the song: auto-scroll keeps following ----
    st["pos"] = 258.0   # filler line 34 sits at 155 + 34*3 = 257s
    pg.wait_for_timeout(2200)
    active = pg.eval_on_selector_all("#lyrics .line.active", "els => els.map(e=>e.textContent)")
    check("follows deep into song", active == ["Filler lyric line number 34"], active)
    check("chip follows the voice change",
          "Band A" in pg.inner_text("#singer") and "Singer B" not in pg.inner_text("#singer"),
          pg.inner_text("#singer"))
    centered = pg.eval_on_selector(
        "#lyrics",
        "e => { const a = e.querySelector('.line.active').getBoundingClientRect();"
        "       const c = e.getBoundingClientRect();"
        "       return (a.top - c.top) / c.height; }")
    check("active line kept in view", 0.15 < centered < 0.65, centered)
    pg.screenshot(path=os.path.join(HERE, "shot_2b_deep.png"))

    # ---- a real user scroll pauses auto-follow, then it resumes ----
    scroll_before = pg.eval_on_selector("#lyrics", "e => e.scrollTop")
    pg.mouse.move(400, 500)
    pg.mouse.wheel(0, -600)
    pg.wait_for_timeout(1200)
    st["pos"] = 264.0
    pg.wait_for_timeout(1500)
    during = pg.eval_on_selector("#lyrics", "e => e.scrollTop")
    check("user scroll pauses auto-follow", during < scroll_before - 100,
          (scroll_before, during))
    pg.wait_for_timeout(4200)
    st["pos"] = 270.0
    pg.wait_for_timeout(2000)
    after = pg.eval_on_selector("#lyrics", "e => e.scrollTop")
    check("auto-follow resumes after 4s idle", after > during, (during, after))

    st["pos"] = 151.0
    pg.wait_for_timeout(1500)

    # ---- progress bar ----
    w = pg.eval_on_selector("#progress > div", "e => e.style.width")
    check("progress bar tracks position",
          abs(float(w.rstrip('%')) - 151 / 302 * 100) < 1.5, w)

    # ---- sync offset keybinding ----
    pg.keyboard.press("[")
    pg.wait_for_timeout(300)
    off = pg.inner_text("#offset")
    check("offset keybinding works", "-0.25" in off, off)
    pg.keyboard.press("0")
    pg.wait_for_timeout(200)
    check("offset reset works", "+0.00" in pg.inner_text("#offset"),
          pg.inner_text("#offset"))

    # ---- pause freezes highlight ----
    st["playing"] = False
    # Pausing has to travel server poll (0.4s) then client poll (1.0s) before
    # the page stops interpolating. 1200ms was inside that window, so this
    # sampled a still-advancing bar about half the time.
    pg.wait_for_timeout(2600)
    p1 = pg.eval_on_selector("#progress > div", "e => e.style.width")
    pg.wait_for_timeout(1500)
    p2 = pg.eval_on_selector("#progress > div", "e => e.style.width")
    check("paused freezes progress", p1 == p2, (p1, p2))
    check("paused badge", "paused" in pg.inner_text("#badges").lower(),
          pg.inner_text("#badges"))
    st["playing"] = True

    # ---- switch to a track with no lyrics ----
    st["track"] = NOLYR_TRACK
    st["pos"] = 5.0
    pg.wait_for_timeout(3000)
    check("new title", "Unknown B-Side" in pg.inner_text("#title"), pg.inner_text("#title"))
    check("chip cleared on a track without credits", not pg.is_visible("#singer"),
          pg.inner_text("#singer"))
    check("no-lyrics message", "No lyrics found" in pg.inner_text("#lyrics"),
          pg.inner_text("#lyrics")[:120])
    check("badge says no lyrics", "no lyrics" in pg.inner_text("#badges").lower(),
          pg.inner_text("#badges"))
    check("translate button hidden when there are no lyrics",
          not pg.is_visible("#tbtn"), pg.inner_text("#tbtn"))
    pg.screenshot(path=os.path.join(HERE, "shot_3_nolyrics.png"))

    # ---- spotify closed ----
    st["status"] = "not_running"
    pg.wait_for_timeout(2000)
    check("spotify-closed screen", "isn" in pg.inner_text("#status"),
          pg.inner_text("#status")[:120])
    pg.screenshot(path=os.path.join(HERE, "shot_4_closed.png"))

    # ---- recovers when spotify comes back ----
    st["status"] = "ok"; st["track"] = LYRIC_TRACK; st["pos"] = 16.0
    pg.wait_for_timeout(3000)
    check("recovers after Spotify reopens",
          pg.eval_on_selector_all("#lyrics .line", "e => e.length") == 47,
          pg.eval_on_selector_all("#lyrics .line", "e => e.length"))

    # ---- narrow viewport ----
    pg.set_viewport_size({"width": 900, "height": 800})
    pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(HERE, "shot_5_narrow.png"))
    pg.set_viewport_size({"width": 1440, "height": 900})

    # ---- translate button ----
    check("translate button visible with lyrics",
          pg.is_visible("#tbtn"), pg.eval_on_selector("#tbtn", "e => e.hidden"))
    check("button starts as 'Translate'",
          pg.inner_text("#tbtn").strip() == "Translate", pg.inner_text("#tbtn"))
    check("no translations rendered yet",
          pg.eval_on_selector_all(".tr", "e => e.length") == 0)

    pg.click("#tbtn")
    pg.wait_for_timeout(2500)
    check("translations rendered under lines",
          pg.eval_on_selector_all(".tr", "e => e.length") == 2,
          pg.eval_on_selector_all(".tr", "e => e.length"))
    check("low-confidence line flagged uncertain",
          pg.eval_on_selector_all(".tr.unsure", "e => e.length") == 1,
          pg.eval_on_selector_all(".tr.unsure", "e => e.length"))
    check("note rendered", "wahala = trouble" in pg.inner_text("#lyrics"))
    check("button flips to Hide",
          pg.inner_text("#tbtn").strip() == "Hide translation", pg.inner_text("#tbtn"))
    check("button marked active",
          pg.eval_on_selector("#tbtn", "e => e.classList.contains('on')"))
    pg.screenshot(path=os.path.join(HERE, "shot_7_translated.png"))

    pg.click("#tbtn")
    pg.wait_for_timeout(600)
    check("hiding removes the translations",
          pg.eval_on_selector_all(".tr", "e => e.length") == 0)
    check("button flips to Show",
          pg.inner_text("#tbtn").strip() == "Show translation", pg.inner_text("#tbtn"))

    pg.click("#tbtn")
    pg.wait_for_timeout(600)
    check("re-showing does not refetch", pg.eval_on_selector_all(".tr", "e => e.length") == 2)

    # the `t` key must stay equivalent to the button
    pg.keyboard.press("t")
    pg.wait_for_timeout(500)
    check("`t` key still toggles", pg.eval_on_selector_all(".tr", "e => e.length") == 0)
    check("key and button share one label",
          pg.inner_text("#tbtn").strip() == "Show translation", pg.inner_text("#tbtn"))

    # ---- tap-to-sync on a plain-lyrics track ----
    # Playback is paused for this section on purpose: while playing, the page
    # interpolates position between polls, so a tapped timestamp can't be
    # compared to an exact expected value.
    published.clear()
    st["track"] = PLAIN_TRACK
    st["playing"] = False
    st["pos"] = 0.0
    pg.wait_for_timeout(3000)
    check("translate button offered on untimed lyrics too",
          pg.is_visible("#tbtn"), pg.inner_text("#tbtn"))
    check("plain lyrics rendered",
          pg.eval_on_selector_all("#lyrics .line", "e => e.length") == 4,
          pg.eval_on_selector_all("#lyrics .line", "e => e.length"))
    check("sync is offered", "Press" in pg.inner_text("#sync"), pg.inner_text("#sync"))

    pg.keyboard.press("s")
    pg.wait_for_timeout(400)
    check("sync mode entered", "Sync mode" in pg.inner_text("#sync"),
          pg.inner_text("#sync"))
    check("first line is the target",
          pg.eval_on_selector_all("#lyrics .line.active",
                                  "e => e.map(x => x.textContent)") == ["plain one"])

    for pos in (3.0, 8.0, 14.0, 21.0):
        st["pos"] = pos
        pg.wait_for_timeout(1900)      # server poll + client poll
        pg.keyboard.press(" ")
        pg.wait_for_timeout(350)
    pg.wait_for_timeout(1800)
    check("saved after the last line", "Timed 4 of 4" in pg.inner_text("#sync"),
          pg.inner_text("#sync"))
    pg.screenshot(path=os.path.join(HERE, "shot_6_synced.png"))

    det = eng.track_detail(PLAIN_TRACK["id"])
    ts = [l["t"] for l in det["lyrics"]["lines"]]
    check("promoted to synced", det["lyrics"]["kind"] == "synced",
          det["lyrics"]["kind"])
    check("timestamps ascending", all(ts[i] < ts[i + 1] for i in range(3)), ts)
    check("tap-lead of 0.25s applied",
          all(abs((e - t) - 0.25) < 0.05 for t, e in zip(ts, [3, 8, 14, 21])), ts)
    # This is what broke first time: loadDetail only re-renders when it has
    # nothing yet, so the page kept showing the stale plain copy.
    check("page switched to the synced copy",
          "synced" in pg.inner_text("#badges").lower(), pg.inner_text("#badges"))

    check("saving did not publish", published == [], published)
    pg.keyboard.press("p")
    pg.wait_for_timeout(2500)
    check("publish only on explicit press", len(published) == 1, published)
    check("published LRC is well-formed",
          published and published[0].startswith("[00:02.75]plain one"), published)

    check("no JS errors overall", not errors, errors)
    b.close()

srv.shutdown()
print("\n" + ("ALL UI TESTS PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
