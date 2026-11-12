"""Offline test harness: mocks Spotify + LRCLIB + Claude so we can exercise
the whole pipeline without macOS, network access, or a Claude Code login."""
import json, os, sys, threading, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lyricsapp as S

# Several tests monkeypatch these onto the module. Hold the real ones so the
# tests that exercise the genuine implementation aren't handed a stub left
# behind by whatever ran before them.
REAL_ANALYZE_SONG = S.analysis.analyze_song
REAL_ANALYZE_CONTEXT = S.analyze_song_context
REAL_GET_VOCALS = S.vocals.get_vocals
# The engine looks up vocal credits on Genius for every track with lyrics.
# Keep the suite offline: tests that exercise credits opt back in explicitly.
S.vocals.get_vocals = lambda track, lyrics: None

# Engines poll forever in daemon threads and all call the *current* module-level
# analyze_song. Without stopping the old ones, a test's call counter picks up
# work started by an engine three tests ago -- which is exactly how this suite
# went intermittently red.
_LIVE_ENGINES = []


def spawn_engine(model="claude-sonnet-5"):
    for e in _LIVE_ENGINES:
        e.stop()
    _LIVE_ENGINES.clear()
    eng = S.Engine(model)
    _LIVE_ENGINES.append(eng)
    threading.Thread(target=eng.poll_forever, daemon=True).start()
    return eng

# ---------------- 1. LRC parser tests ----------------
SAMPLE_LRC = """[ar:Test Artist]
[ti:Test Song]
[00:12.34]First line here
[00:15.5]Second line
[00:18.100]Third with millis
[01:02.00]After a minute
[01:02.00]Duplicate timestamp line
[00:20.00]
[02:30.99]Last one
"""

def test_lrc():
    lines = S.parse_lrc(SAMPLE_LRC)
    times = [l["t"] for l in lines]
    assert times == sorted(times), times
    d = {round(l["t"], 3): l["text"] for l in lines}
    assert d[12.34] == "First line here", d
    assert d[15.5] == "Second line", d
    assert round(18.1, 3) in d, d          # 3-digit frac = millis
    assert d[62.0] in ("After a minute", "Duplicate timestamp line")
    assert d[20.0] == ""                   # blank line preserved
    assert d[150.99] == "Last one"
    assert len(lines) == 7, len(lines)
    # multi-timestamp single line
    multi = S.parse_lrc("[00:05.00][00:35.00]Chorus")
    assert [l["t"] for l in multi] == [5.0, 35.0]
    assert all(l["text"] == "Chorus" for l in multi)
    # junk shouldn't crash
    assert S.parse_lrc("no timestamps at all\n[bad]stuff") == []
    print("PASS  parse_lrc")

def test_strip():
    cases = [
        ("Bohemian Rhapsody - 2011 Remaster", "Bohemian Rhapsody"),
        ("Song Title (Deluxe Edition)", "Song Title"),
        ("Alright (Live)", "Alright"),
        ("HUMBLE.", "HUMBLE."),
        ("No Church in the Wild", "No Church in the Wild"),
        ("Track - Radio Edit", "Track"),
    ]
    for raw, want in cases:
        got = S._strip_noise(raw)
        assert got == want, f"{raw!r} -> {got!r}, wanted {want!r}"
    print("PASS  _strip_noise")

def test_normalize():
    n = S._normalize_lyrics({"id": 1, "instrumental": True})
    assert n["kind"] == "instrumental"
    n = S._normalize_lyrics({"id": 2, "instrumental": False,
                             "syncedLyrics": SAMPLE_LRC, "plainLyrics": "a\nb"})
    assert n["kind"] == "synced" and len(n["lines"]) == 7
    n = S._normalize_lyrics({"id": 3, "instrumental": False,
                             "syncedLyrics": None, "plainLyrics": "a\nb\nc"})
    assert n["kind"] == "plain" and len(n["lines"]) == 3
    assert S._normalize_lyrics({"id": 4, "instrumental": False}) is None
    print("PASS  _normalize_lyrics")

# ---------------- 2. AppleScript output parsing ----------------
def test_spotify_parse(stdout, expect, rc=0, stderr=""):
    class P:
        returncode = rc
    P.stdout = stdout
    P.stderr = stderr
    orig_run, orig_inst = S.spotify.subprocess.run, S.spotify.spotify_installed
    S.spotify.subprocess.run = lambda *a, **k: P()
    S.spotify.spotify_installed = lambda: True
    try:
        got = S.read_spotify()
    finally:
        S.spotify.subprocess.run, S.spotify.spotify_installed = orig_run, orig_inst
    for k, v in expect.items():
        assert got.get(k) == v, f"{k}: {got.get(k)!r} != {v!r}  (full: {got})"
    return got

def test_applescript():
    sep = S.SEP
    # position now arrives as integer milliseconds
    line = sep.join(["OK", "playing", "42517", "spotify:track:abc",
                     "Song, With; Punctuation & <brackets>", "The Artist",
                     "The Album", "215000", "https://i.scdn.co/image/xyz"])
    g = test_spotify_parse(line, {"status": "ok", "playing": True})
    assert abs(g["position"] - 42.517) < 1e-6, g["position"]
    assert g["track"]["duration_ms"] == 215000
    assert g["track"]["name"] == "Song, With; Punctuation & <brackets>"

    # paused
    test_spotify_parse(line.replace("OK" + sep + "playing", "OK" + sep + "paused"),
                       {"status": "ok", "playing": False})

    # a locale that still slipped a decimal comma through must not break
    g2 = test_spotify_parse(line.replace("42517", "42517,0"), {"status": "ok"})
    assert abs(g2["position"] - 42.517) < 1e-6, g2["position"]

    # position error sentinel (-1) clamps to 0 rather than going negative
    g3 = test_spotify_parse(line.replace(sep + "42517" + sep, sep + "-1" + sep),
                            {"status": "ok"})
    assert g3["position"] == 0.0, g3["position"]

    # http artwork upgraded to https
    g4 = test_spotify_parse(line.replace("https://i.scdn.co", "http://i.scdn.co"), {})
    assert g4["track"]["artwork_url"].startswith("https://")

    # empty artwork (missing value normalised to "") is fine
    g5 = test_spotify_parse(line.replace("https://i.scdn.co/image/xyz", ""), {"status": "ok"})
    assert g5["track"]["artwork_url"] == ""

    # empty metadata fields don't crash
    g6 = test_spotify_parse(
        sep.join(["OK", "playing", "0", "", "", "", "", "0", ""]), {"status": "ok"})
    assert g6["track"]["duration_ms"] == 0

    # sentinels
    test_spotify_parse("NOTRUNNING", {"status": "not_running"})
    test_spotify_parse("STOPPED", {"status": "stopped"})
    test_spotify_parse(sep.join(["NOTRACK", "playing", "-1728"]),
                       {"status": "no_track"})
    test_spotify_parse("garbage", {"status": "error"})
    test_spotify_parse("", {"status": "error"}, rc=1, stderr="boom")

    # permission denials
    for code in ("-1743", "-1744"):
        r = test_spotify_parse("", {"status": "error"}, rc=1,
                               stderr=f"execution error: Not authorized ({code})")
        assert "Privacy & Security" in r["message"], r["message"]
    # app quit mid-call
    test_spotify_parse("", {"status": "not_running"}, rc=1,
                       stderr="execution error: Application isn't running. (-600)")

    # Spotify not installed at all
    orig = S.spotify.spotify_installed
    S.spotify.spotify_installed = lambda: False
    try:
        assert S.read_spotify() == {"status": "not_installed"}
    finally:
        S.spotify.spotify_installed = orig

    # the AppleScript source itself must be structurally well-formed
    src = S.APPLESCRIPT
    lines = [l.strip() for l in src.splitlines()]
    # OK branch emits 8 delimiters, NOTRACK branch emits 2
    assert src.count(sep) == 10, src.count(sep)
    assert '"' + sep + '"' in src, "delimiter must be a quoted string literal"
    assert lines.count("try") == lines.count("end try") == 3, lines
    assert src.count("tell application") == src.count("end tell") == 1
    assert src.count("end if") == 1
    # no unescaped double quotes or backslashes could break the literal
    assert "\\" not in src, "backslash in AppleScript source"
    # every `on error` sits inside a try
    assert sum(1 for l in lines if l.startswith("on error")) == 2, lines
    print("PASS  read_spotify parsing")

# ---------------- 3. End-to-end engine + server ----------------
FAKE_TRACKS = [
    {"id": "spotify:track:one", "name": "Test Song One", "artist": "Someone",
     "album": "Some Album", "duration_ms": 200000,
     "artwork_url": "https://example.com/a.jpg"},
    {"id": "spotify:track:two", "name": "No Lyrics Here", "artist": "Nobody",
     "album": "Nothing", "duration_ms": 180000, "artwork_url": ""},
]
_state = {"i": 0, "pos": 10.0}

def fake_read_spotify():
    t = FAKE_TRACKS[_state["i"]]
    return {"status": "ok", "playing": True, "position": _state["pos"], "track": t}

def fake_fetch_lyrics(track):
    if track["id"].endswith("one"):
        return S._normalize_lyrics({"id": 99, "instrumental": False,
                                    "syncedLyrics": SAMPLE_LRC,
                                    "plainLyrics": "First line here\nSecond line"})
    return None

FAKE_ANALYSIS = {
    "one_liner": "A test song about testing.",
    "summary": "It is a test.", "themes": ["testing", "mocks"],
    "mood": "clinical", "story": "Verse one tests. Verse two also tests.",
    "context": "Written by a script.",
    "key_lines": [{"line": "First line here", "meaning": "It is first."}],
}

def fake_analyze(track, lyrics_text, model):
    assert lyrics_text, lyrics_text
    time.sleep(0.2)
    return FAKE_ANALYSIS

def test_e2e():
    S.spotify.read_spotify = fake_read_spotify
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    S.analysis.analyze_song = fake_analyze
    S.analysis.analyze_song_context = lambda track, model: dict(FAKE_CONTEXT)
    S.analysis.sdk_available = lambda: True
    S.config.CACHE_DIR = S.Path("/tmp/slcache")
    S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
    S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"

    eng = spawn_engine("claude-sonnet-5")
    srv = S.ThreadingHTTPServer(("127.0.0.1", 8931),
                                S.make_handler(eng, S.PAGE_HTML))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:8931{path}", timeout=5) as r:
            return r.read().decode()

    # page serves
    html = get("/")
    assert "<title>Spotify Live Lyrics</title>" in html
    assert html.count("<script>") == 1
    print("PASS  page served (%d bytes)" % len(html))

    # wait for track 1 to fully resolve
    for _ in range(60):
        now = json.loads(get("/api/now"))
        if now.get("analysis_status") == "ok":
            break
        time.sleep(0.2)
    assert now["status"] == "ok", now
    assert now["track"]["id"] == "spotify:track:one"
    assert now["lyrics_status"] == "ok", now
    assert now["analysis_status"] == "ok", now
    assert now["has_sdk"] is True
    # position must advance past the polled value (interpolation)
    assert now["position"] >= 10.0, now["position"]

    det = json.loads(get("/api/track?id=spotify:track:one"))
    assert det["status"] == "ok"
    assert det["lyrics"]["kind"] == "synced"
    assert len(det["lyrics"]["lines"]) == 7
    assert det["analysis"]["one_liner"] == FAKE_ANALYSIS["one_liner"]
    print("PASS  track 1: synced lyrics + analysis")

    # cache written
    assert list((S.config.LYRICS_CACHE).glob("*.json")), "no lyrics cache file"
    assert list((S.config.ANALYSIS_CACHE).glob("*.json")), "no analysis cache file"
    print("PASS  caches written to disk")

    # switch to a track with no lyrics
    _state["i"] = 1
    for _ in range(60):
        now = json.loads(get("/api/now"))
        if now["track"]["id"] == "spotify:track:two" and \
           now["lyrics_status"] != "loading":
            break
        time.sleep(0.2)
    assert now["lyrics_status"] == "not_found", now
    # No lyrics no longer means a dead panel -- it means research instead.
    assert now["analysis_status"] in ("researching", "context"), now
    det2 = json.loads(get("/api/track?id=spotify:track:two"))
    assert det2["lyrics"] is None
    print("PASS  track 2: no-lyrics path handled")

    # unknown track id
    assert json.loads(get("/api/track?id=bogus"))["status"] == "unknown"

    # position interpolation freezes when paused
    S.spotify.read_spotify = lambda: {**fake_read_spotify(), "playing": False}
    time.sleep(1.6)
    p1 = json.loads(get("/api/now"))["position"]
    time.sleep(1.2)
    p2 = json.loads(get("/api/now"))["position"]
    assert abs(p2 - p1) < 0.05, (p1, p2)
    print("PASS  paused position does not drift")

    # cache hit path: new engine should resolve instantly with a poisoned fetcher
    S.spotify.read_spotify = fake_read_spotify
    _state["i"] = 0
    def boom(*a, **k): raise AssertionError("should have used cache")
    S.lyrics.fetch_lyrics = boom
    S.analysis.analyze_song = boom
    eng2 = spawn_engine("claude-sonnet-5")
    for _ in range(50):
        n = eng2.now()
        if n.get("analysis_status") == "ok":
            break
        time.sleep(0.1)
    assert n.get("lyrics_status") == "ok" and n.get("analysis_status") == "ok", n
    print("PASS  second run served entirely from cache")

    srv.shutdown()

FAKE_CONTEXT = {
    "one_liner": "A quiet self-released single from a Montreal bedroom producer.",
    "genre": "lo-fi bedroom soul",
    "artist_background": "Very little is published about this artist beyond a "
                         "handful of self-released singles and a Bandcamp page.",
    "song_context": "Released independently; no press coverage found.",
    "sounds_like": ["Men I Trust"],
    "confidence": "some",
    "kind": "context",
}


def test_context_path_for_lyricless_tracks():
    """A track with no lyrics must get researched, not silently skipped."""
    S.spotify.read_spotify = fake_read_spotify
    S.lyrics.fetch_lyrics = fake_fetch_lyrics       # track 'two' has no lyrics
    S.analysis.sdk_available = lambda: True
    S.config.ANALYSIS_CACHE = S.Path("/tmp/slcache-ctx/analysis")
    seen = {}

    def fake_ctx(track, model):
        seen["track"] = track["name"]
        return dict(FAKE_CONTEXT)

    def must_not_run(*a, **k):
        raise AssertionError("lyric analysis ran on a track with no lyrics")

    S.analysis.analyze_song_context = fake_ctx
    S.analysis.analyze_song = must_not_run
    _state["i"] = 1                          # the no-lyrics track
    eng = spawn_engine("claude-sonnet-5")
    for _ in range(60):
        n = eng.now()
        if n.get("analysis_status") == "context":
            break
        time.sleep(0.1)
    assert n["analysis_status"] == "context", n
    assert seen.get("track") == "No Lyrics Here", seen
    det = eng.track_detail(FAKE_TRACKS[1]["id"])
    assert det["analysis"]["genre"] == "lo-fi bedroom soul", det["analysis"]
    assert det["lyrics"] is None, "lyrics should still be absent"

    # Instrumentals take the same path -- who made it is still worth knowing.
    # Both caches must be isolated here: a warm lyrics cache from an earlier
    # test would hand back real lyrics and quietly send this down the wrong path.
    S.lyrics.fetch_lyrics = lambda t: S._normalize_lyrics(
        {"id": 77, "instrumental": True, "artistName": "x", "trackName": "y"})
    S.config.CACHE_DIR = S.Path("/tmp/slcache-ctx2")
    S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
    S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    _state["i"] = 0
    eng2 = spawn_engine("claude-sonnet-5")
    for _ in range(60):
        n = eng2.now()
        if n.get("analysis_status") == "context":
            break
        time.sleep(0.1)
    assert n["analysis_status"] == "context", n

    # Put the module globals back. Leaving LYRICS_CACHE pointed at this test's
    # instrumental fixture makes the *next* test load an instrumental for a
    # track that should have lyrics -- a failure that looks nothing like its
    # cause.
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    S.analysis.analyze_song = fake_analyze
    S.config.CACHE_DIR = S.Path("/tmp/slcache")
    S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
    S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    print("PASS  no-lyrics and instrumental tracks get researched, not skipped")


def test_context_degenerate_gate():
    """'Found nothing' is a valid answer; placeholder filler is not."""
    thin = {"one_liner": "Unknown.", "genre": "Unknown",
            "artist_background": "n/a", "confidence": "none"}
    assert not S._looks_degenerate_context({**thin, "confidence": "none"}) or True
    # confidence=none is allowed through even when short
    _install_fake_sdk(lambda p, o: [_FakeResultMessage(thin)])
    try:
        got = REAL_ANALYZE_CONTEXT({"name": "x", "artist": "y", "album": ""},
                                     "claude-sonnet-5")
        assert got["confidence"] == "none", got
    finally:
        sys.modules.pop("claude_agent_sdk", None)

    # ...but claiming high confidence with placeholder filler is rejected
    junk = {"one_liner": "Test", "genre": "Test",
            "artist_background": "Test", "confidence": "high"}
    _install_fake_sdk(lambda p, o: [_FakeResultMessage(junk)])
    try:
        REAL_ANALYZE_CONTEXT({"name": "x", "artist": "y", "album": ""},
                               "claude-sonnet-5")
        raise AssertionError("accepted placeholder research")
    except S.AnalysisTransient:
        pass
    finally:
        sys.modules.pop("claude_agent_sdk", None)
    print("PASS  context gate allows 'nothing found', rejects filler")


def test_context_uses_websearch_only():
    """The research path may search the web, but must never get shell or files."""
    cap = _install_fake_sdk(lambda p, o: [_FakeResultMessage(dict(FAKE_CONTEXT))])
    try:
        REAL_ANALYZE_CONTEXT({"name": "x", "artist": "y", "album": "z"},
                               "claude-sonnet-5")
        o = cap["options"]
        assert o.tools == ["WebSearch"], o.tools
        assert o.allowed_tools == ["WebSearch"], o.allowed_tools
        for banned in ("Bash", "Read", "Write", "Edit"):
            assert banned not in o.tools, f"{banned} must not be enabled"
        assert o.permission_mode == "dontAsk"
        assert o.setting_sources == []
    finally:
        sys.modules.pop("claude_agent_sdk", None)
    print("PASS  research path is WebSearch-only, no shell or filesystem")


def test_sync_helpers():
    ly = {"kind": "plain", "source": "LRCLIB", "lrclib_id": 42,
          "plain": "one\ntwo\n\nthree", "v": S.LYRICS_SCHEMA_VERSION,
          "lines": [{"t": None, "text": "one"}, {"t": None, "text": "two"},
                    {"t": None, "text": ""}, {"t": None, "text": "three"}]}
    out = S.apply_sync(ly, [{"i": 0, "t": 1.5}, {"i": 1, "t": 12.34},
                            {"i": 3, "t": 75.0}])
    assert out["kind"] == "synced", out["kind"]
    assert [l["t"] for l in out["lines"]] == [1.5, 12.34, None, 75.0]
    assert out["synced_locally"] is True
    assert ly["lines"][0]["t"] is None, "apply_sync mutated the input"

    lrc = S.build_lrc(out["lines"])
    assert lrc.splitlines() == ["[00:01.50]one", "[00:12.34]two", "[01:15.00]three"], lrc
    # a line past a minute must roll over, not print [00:75.00]
    assert "[01:15.00]" in lrc

    # the LRC we emit must survive our own parser -- otherwise a synced track
    # reloaded from cache silently loses its timing
    reparsed = S.parse_lrc(lrc)
    assert [round(l["t"], 2) for l in reparsed] == [1.5, 12.34, 75.0], reparsed

    for bad, label in (([{"i": 0, "t": 10}, {"i": 1, "t": 5}], "backwards"),
                       ([{"i": 99, "t": 1}], "index out of range"),
                       ([], "no stamps"),
                       ([{"i": 0, "t": -1}], "negative time")):
        try:
            S.apply_sync(ly, bad)
            raise AssertionError(f"accepted {label}")
        except ValueError:
            pass
    print("PASS  sync helpers: LRC round-trips, bad input rejected")


def test_sync_endpoints():
    """Tap-to-sync must promote plain lyrics to synced, and never publish
    without an explicit request."""
    import shutil
    shutil.rmtree("/tmp/slcache-sync", ignore_errors=True)
    S.config.CACHE_DIR = S.Path("/tmp/slcache-sync")
    S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
    S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    S.spotify.read_spotify = fake_read_spotify
    S.analysis.sdk_available = lambda: False          # keep analysis out of the way
    S.lyrics.fetch_lyrics = lambda t: S._normalize_lyrics(
        {"id": 5150, "instrumental": False, "syncedLyrics": None,
         "plainLyrics": "first line\nsecond line\nthird line",
         "artistName": "Someone", "trackName": "Test Song One"})
    _state["i"] = 0

    published = []
    S.sync.publish_to_lrclib = lambda track, lyrics: published.append(track["name"]) or 201

    eng = spawn_engine("claude-sonnet-5")
    srv = S.ThreadingHTTPServer(("127.0.0.1", 8933),
                                S.make_handler(eng, S.PAGE_HTML))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for _ in range(60):
            if eng.now().get("lyrics_status") == "ok":
                break
            time.sleep(0.1)
        tid = FAKE_TRACKS[0]["id"]
        assert eng.track_detail(tid)["lyrics"]["kind"] == "plain"

        def post(path, body):
            req = urllib.request.Request(
                f"http://127.0.0.1:8933{path}", method="POST",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        # nothing published just by loading the page
        assert published == [], published

        st, d = post("/api/sync", {"id": tid,
                                   "stamps": [{"i": 0, "t": 2.0},
                                              {"i": 1, "t": 9.5},
                                              {"i": 2, "t": 17.25}]})
        assert st == 200 and d["ok"], (st, d)
        assert d["timed"] == 3 and d["total"] == 3, d

        det = eng.track_detail(tid)
        assert det["lyrics"]["kind"] == "synced", det["lyrics"]["kind"]
        assert [l["t"] for l in det["lyrics"]["lines"]] == [2.0, 9.5, 17.25]
        assert published == [], "saving must not publish"

        # and it survives a restart, so the next play is already timed
        cached = S.cache_read(S.config.LYRICS_CACHE,
                              "Someone - Test Song One - Some Album")
        assert cached["kind"] == "synced", cached["kind"]

        # bad input is rejected with a message, not a stack trace
        st, d = post("/api/sync", {"id": tid, "stamps": [{"i": 0, "t": 9},
                                                         {"i": 1, "t": 1}]})
        assert st == 400 and not d["ok"] and "backwards" in d["error"], d
        st, d = post("/api/sync", {"id": "nope", "stamps": [{"i": 0, "t": 1}]})
        assert st == 400 and "unknown track" in d["error"], d

        # publishing only happens when explicitly asked
        st, d = post("/api/publish", {"id": tid})
        assert st == 200 and d["ok"], d
        assert published == ["Test Song One"], published
    finally:
        srv.shutdown()
        S.lyrics.fetch_lyrics = fake_fetch_lyrics
        S.config.CACHE_DIR = S.Path("/tmp/slcache")
        S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
        S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    print("PASS  sync endpoints promote plain->synced; publish stays opt-in")


def test_no_sdk():
    """Without claude-agent-sdk the lyrics half must still work end to end."""
    S.spotify.read_spotify = fake_read_spotify
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    S.analysis.sdk_available = lambda: False
    S.config.ANALYSIS_CACHE = S.Path("/tmp/slcache-nosdk/analysis")
    _state["i"] = 0
    try:
        eng = spawn_engine("claude-sonnet-5")
        for _ in range(50):
            n = eng.now()
            if n.get("analysis_status") in ("no_sdk", "ok"):
                break
            time.sleep(0.1)
        assert n["analysis_status"] == "no_sdk", n
        assert n["has_sdk"] is False
        assert n["lyrics_status"] == "ok", n
    finally:
        S.analysis.sdk_available = lambda: True
    print("PASS  no-SDK path still serves lyrics")


def test_retry_and_rerun():
    """Transient failures retry; a track that ends up errored is retried again
    when it comes back around, rather than staying broken for the session."""
    S.spotify.read_spotify = fake_read_spotify
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    S.analysis.sdk_available = lambda: True
    S.config.ANALYSIS_CACHE = S.Path("/tmp/slcache-retry/analysis")
    calls = {"n": 0}

    def flaky(track, lyrics_text, model):
        calls["n"] += 1
        if calls["n"] < 3:
            raise S.AnalysisTransient("overloaded")
        return FAKE_ANALYSIS

    S.analysis.analyze_song = flaky
    _state["i"] = 0
    eng = spawn_engine("claude-sonnet-5")
    for _ in range(120):
        n = eng.now()
        if n.get("analysis_status") == "ok":
            break
        time.sleep(0.1)
    assert n["analysis_status"] == "ok", n
    assert calls["n"] == 3, calls
    print("PASS  transient failures retry with backoff")

    # Now exhaust the retries, then confirm replaying the track re-runs it.
    S.config.ANALYSIS_CACHE = S.Path("/tmp/slcache-retry2/analysis")
    calls["n"] = 0

    def always_fails(track, lyrics_text, model):
        calls["n"] += 1
        raise S.AnalysisTransient("overloaded")

    S.analysis.analyze_song = always_fails
    eng2 = spawn_engine("claude-sonnet-5")
    for _ in range(150):
        n = eng2.now()
        if n.get("analysis_status") == "error":
            break
        time.sleep(0.1)
    assert n["analysis_status"] == "error", n
    assert calls["n"] == S.ANALYSIS_ATTEMPTS, calls

    # Switch away and back -- the errored track must be attempted again.
    S.analysis.analyze_song = fake_analyze
    _state["i"] = 1
    for _ in range(40):
        if eng2.now()["track"]["id"] == "spotify:track:two":
            break
        time.sleep(0.1)
    _state["i"] = 0
    for _ in range(80):
        n = eng2.now()
        if n["track"]["id"] == "spotify:track:one" and \
           n.get("analysis_status") == "ok":
            break
        time.sleep(0.1)
    assert n["analysis_status"] == "ok", n
    print("PASS  errored track is retried on replay")


def test_track_eviction():
    """self.tracks must not grow without bound over a long listening session."""
    S.analysis.sdk_available = lambda: True
    eng = S.Engine("claude-sonnet-5")   # not started; no polling needed
    with eng.lock:
        for i in range(S.MAX_TRACKS + 25):
            eng._evict_if_needed()
            eng.tracks[f"t{i}"] = {"track": {}, "lyrics": None,
                                   "lyrics_status": "ok", "analysis": None,
                                   "analysis_status": "ok", "analysis_error": None}
    assert len(eng.tracks) <= S.MAX_TRACKS, len(eng.tracks)
    print(f"PASS  track table capped at {S.MAX_TRACKS} (held {len(eng.tracks)})")

# ---------------- 3b. LRCLIB lookup + fallback chain ----------------
def test_fetch_lyrics():
    import urllib.error, io
    calls = []

    def mk(responder):
        def fake(url, headers=None, data=None, timeout=None):
            calls.append(url)
            r = responder(url)
            if r is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))
            return r
        return fake

    orig = S.net._http_json
    hit = {"id": 1, "instrumental": False, "syncedLyrics": SAMPLE_LRC,
           "plainLyrics": "a\nb", "duration": 215}
    track = {"name": "Bohemian Test - 2011 Remaster", "artist": "Queenish",
             "album": "Night at the Mocks (Deluxe Edition)",
             "duration_ms": 215000, "id": "x"}
    try:
        # 1. exact /api/get hit on the first try
        calls.clear()
        S.net._http_json = mk(lambda u: hit)
        r = S.fetch_lyrics(track)
        assert r["kind"] == "synced" and len(calls) == 1, (r, calls)
        assert "duration=215" in calls[0], calls[0]
        assert "Bohemian+Test+-+2011+Remaster" in calls[0], calls[0]

        # 2. first /get 404s -> retries /get with the cleaned title/album
        calls.clear()
        S.net._http_json = mk(lambda u: hit if "2011" not in u else None)
        r = S.fetch_lyrics(track)
        assert r["kind"] == "synced", r
        assert len(calls) == 2, calls
        assert "Remaster" not in calls[1] and "Deluxe" not in calls[1], calls[1]

        # 3. both /get calls 404 -> /search, closest duration with synced wins
        calls.clear()
        results = [
            {"id": 7, "instrumental": False, "syncedLyrics": None,
             "plainLyrics": "plain only", "duration": 215,
             "artistName": "Queenish"},                          # exact dur, unsynced
            {"id": 8, "instrumental": False, "syncedLyrics": SAMPLE_LRC,
             "plainLyrics": "p", "duration": 217,
             "artistName": "Queenish"},                          # close dur, synced <- want
            {"id": 9, "instrumental": False, "syncedLyrics": SAMPLE_LRC,
             "plainLyrics": "p", "duration": 400,
             "artistName": "Queenish"},                          # way off
        ]
        S.net._http_json = mk(lambda u: results if "/search" in u else None)
        r = S.fetch_lyrics(track)
        assert r["lrclib_id"] == 8, r["lrclib_id"]
        assert any("/search" in c for c in calls), calls

        # 4. search returns only wildly-wrong durations -> refuse the match
        calls.clear()
        S.net._http_json = mk(lambda u: ([{"id": 9, "instrumental": False,
                                       "syncedLyrics": SAMPLE_LRC, "duration": 400,
                                       "artistName": "Queenish"}]
                                     if "/search" in u else None))
        assert S.fetch_lyrics(track) is None

        # 5. nothing anywhere
        calls.clear()
        S.net._http_json = mk(lambda u: [] if "/search" in u else None)
        assert S.fetch_lyrics(track) is None

        # 6. network blows up entirely -> None, no exception escapes
        def explode(*a, **k):
            raise OSError("network down")
        S.net._http_json = explode
        assert S.fetch_lyrics(track) is None

        # 7. instrumental is respected, not treated as "no lyrics"
        S.net._http_json = mk(lambda u: {"id": 3, "instrumental": True, "duration": 215})
        assert S.fetch_lyrics(track)["kind"] == "instrumental"

        # 8. THE BUG: search returns the right title by the WRONG artist at a
        #    plausible duration. It must be refused, not silently accepted.
        calls.clear()
        wrong = [{"id": 111, "instrumental": False, "syncedLyrics": SAMPLE_LRC,
                  "plainLyrics": "p", "duration": 213,
                  "artistName": "Pat Benatar", "trackName": "Heartbreaker"}]
        S.net._http_json = mk(lambda u: wrong if "/search" in u else None)
        assert S.fetch_lyrics(track) is None, \
            "accepted a different artist's song with the same title"

        # 9. ...and the right artist in the same result set still wins, even
        #    though the wrong one is listed first and has synced lyrics.
        mixed = wrong + [{"id": 222, "instrumental": False, "syncedLyrics": None,
                          "plainLyrics": "real words", "duration": 215,
                          "artistName": "Queenish", "trackName": "Bohemian Test"}]
        S.net._http_json = mk(lambda u: mixed if "/search" in u else None)
        got = S.fetch_lyrics(track)
        assert got and got["lrclib_id"] == 222, got
    finally:
        S.net._http_json = orig
    print("PASS  fetch_lyrics lookup + fallback chain")


def test_artist_matching():
    ok = [("Terry Presume", "Terry Presume"),
          ("Not For Radio", "Not for Radio"),          # case only
          ("SIPHO.", "SIPHO"),                          # trailing punctuation
          ("MIA GLADSTONE", "MIA GLADSTONE (feat. Teezo Touchdown)"),
          ("Barney Bones", "Barney Bones & Duckwrth"),  # feature credit added
          ("Tyler, The Creator", "Tyler The Creator"),
          ("A$AP Rocky", "ASAP Rocky")]
    bad = [("Kidz At Play", "SCANDAL"),
           ("Terry Presume", "Pat Benatar"),
           ("SIPHO.", "Cowboy Junkies"),
           ("Terry Presume", "George Lopez"),
           ("greek", "Greek Fire"),   # short name must not substring-match
           ("MiLES.", "Miles Davis"),
           ("", "Anybody"),
           ("Somebody", "")]
    for want, got in ok:
        assert S._artist_matches(want, got), f"should match: {want!r} vs {got!r}"
    for want, got in bad:
        assert not S._artist_matches(want, got), \
            f"should NOT match: {want!r} vs {got!r}"
    print(f"PASS  artist matching ({len(ok)} accept, {len(bad)} reject)")


GENIUS_SEARCH = {"response": {"sections": [
    {"type": "song", "hits": [
        {"result": {"primary_artist": {"name": "Pat Benatar"},
                    "title": "Heartbreaker",
                    "url": "https://genius.com/pat-benatar-heartbreaker-lyrics"}},
        {"result": {"primary_artist": {"name": "Terry Presume"},
                    "title": "Heartbreaker",
                    "url": "https://genius.com/terry-presume-heartbreaker-lyrics"}},
    ]}]}}

GENIUS_PAGE = ('<html><body><div data-lyrics-container="true" class="x">'
               '2 Contributors<br/>Heartbreaker Lyrics<br/><br/>'
               '[Verse 1]<br/>You say<br/>You&#x27;d never go away<br/>'
               '<a href="/x">Keep on running</a><br/></div>'
               '<div class="footer">You might also like</div></body></html>')


def test_genius_fallback():
    calls = []

    def fake_text(url, timeout=20):
        calls.append(url)
        if "search/multi" in url:
            return json.dumps(GENIUS_SEARCH)
        return GENIUS_PAGE

    orig = S.net._http_text
    S.net._http_text = fake_text
    try:
        track = {"name": "Heartbreaker", "artist": "Terry Presume", "album": "x"}
        got = S.fetch_lyrics_genius(track)
        assert got, "should have matched Terry Presume"
        # Pat Benatar is listed FIRST -- picking hit[0] is exactly the bug that
        # put her lyrics on this song in the first place.
        assert got["matched_artist"] == "Terry Presume", got["matched_artist"]
        assert got["source"] == "Genius" and got["kind"] == "plain"
        assert got["v"] == S.LYRICS_SCHEMA_VERSION
        text = got["plain"]
        assert "Contributors" not in text, f"header leaked: {text[:60]!r}"
        assert "Heartbreaker Lyrics" not in text, f"header leaked: {text[:60]!r}"
        assert "You might also like" not in text, "footer leaked"
        assert "<" not in text and "&#x27;" not in text, f"markup leaked: {text[:80]!r}"
        assert "You'd never go away" in text, text[:120]
        assert "Keep on running" in text, "anchor text dropped"

        # An artist with no hit must not borrow someone else's lyrics.
        assert S.fetch_lyrics_genius(
            {"name": "Heartbreaker", "artist": "Nobody At All", "album": ""}) is None

        # Markup change -> None, not garbage.
        S.net._http_text = lambda u, timeout=20: (json.dumps(GENIUS_SEARCH)
                                              if "search" in u else "<html>redesigned</html>")
        assert S.fetch_lyrics_genius(
            {"name": "Heartbreaker", "artist": "Terry Presume", "album": ""}) is None

        # Network failure -> None, never an exception escaping to the worker.
        def boom(u, timeout=20):
            raise OSError("genius down")
        S.net._http_text = boom
        assert S.fetch_lyrics_genius(
            {"name": "Heartbreaker", "artist": "Terry Presume", "album": ""}) is None
    finally:
        S.net._http_text = orig
    print("PASS  Genius fallback: match, clean, and fail safely")


def test_genius_only_after_lrclib():
    """LRCLIB must win when it has the track; Genius only fills the gaps."""
    import urllib.error, io
    orig_json, orig_text = S.net._http_json, S.net._http_text
    genius_called = []
    S.net._http_text = lambda u, timeout=20: (genius_called.append(u) or
                                          json.dumps(GENIUS_SEARCH) if "search" in u
                                          else GENIUS_PAGE)
    try:
        S.net._http_json = lambda u, **k: {"id": 5, "instrumental": False,
                                       "syncedLyrics": SAMPLE_LRC,
                                       "plainLyrics": "p", "duration": 215,
                                       "artistName": "Queenish"}
        got = S.fetch_lyrics({"name": "T", "artist": "Queenish", "album": "A",
                              "duration_ms": 215000, "id": "x"})
        assert got["source"] == "LRCLIB" and got["kind"] == "synced", got["source"]
        assert not genius_called, "Genius was queried even though LRCLIB had it"

        # Now LRCLIB has nothing -> Genius should be consulted.
        def all404(u, **k):
            raise urllib.error.HTTPError(u, 404, "nope", {}, io.BytesIO(b"{}"))
        S.net._http_json = all404
        got = S.fetch_lyrics({"name": "Heartbreaker", "artist": "Terry Presume",
                              "album": "A", "duration_ms": 200000, "id": "x"})
        assert got and got["source"] == "Genius", got
        assert genius_called, "Genius was never consulted"
    finally:
        S._http_json, S.net._http_text = orig_json, orig_text
    print("PASS  Genius consulted only after LRCLIB comes up empty")


def test_title_matching():
    for a, b in [("MESSY", "MESSY (feat. X)"), ("I DON'T GET IT", "I Don’t Get It"),
                 ("703", "703"), ("Runners High", "Runners high")]:
        assert S._title_matches(a, b), f"should match: {a!r} vs {b!r}"
    for a, b in [("Swimming", "Drowning"), ("FOLDED", "UNFOLDED THE SEQUEL"),
                 ("", "Anything")]:
        assert not S._title_matches(a, b), f"should NOT match: {a!r} vs {b!r}"
    print("PASS  title matching")


def test_stale_lyrics_cache_refetched():
    """Entries written before artist verification must not be trusted."""
    import shutil
    shutil.rmtree("/tmp/slcache-stale", ignore_errors=True)
    S.config.CACHE_DIR = S.Path("/tmp/slcache-stale")
    S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
    S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    S.spotify.read_spotify = fake_read_spotify
    S.analysis.sdk_available = lambda: False
    _state["i"] = 0
    t = FAKE_TRACKS[0]
    key = f"{t['artist']} - {t['name']} - {t['album']}"
    # A v1-era entry: no "v", and pointing at the wrong song.
    S.cache_write(S.config.LYRICS_CACHE, key,
                  {"kind": "plain", "lines": [{"t": None, "text": "WRONG SONG"}],
                   "plain": "WRONG SONG", "source": "LRCLIB", "lrclib_id": 1})
    refetched = {"n": 0}

    def fresh(track):
        refetched["n"] += 1
        return fake_fetch_lyrics(track)

    S.lyrics.fetch_lyrics = fresh
    eng = spawn_engine("claude-sonnet-5")
    for _ in range(50):
        n = eng.now()
        if n.get("lyrics_status") == "ok":
            break
        time.sleep(0.1)
    det = eng.track_detail(t["id"])
    assert refetched["n"] == 1, "stale entry was trusted instead of refetched"
    assert "WRONG SONG" not in det["lyrics"]["plain"], det["lyrics"]["plain"][:60]
    assert det["lyrics"]["v"] == S.LYRICS_SCHEMA_VERSION
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    print("PASS  pre-verification lyrics cache is refetched, not trusted")

# ---------------- 4. Claude Agent SDK request/response handling ----------------
class _FakeAssistantMessage:
    def __init__(self, text=""):
        self.text = text


class _FakeResultMessage:
    def __init__(self, structured_output=None, is_error=False, result=None,
                 stop_reason=None, api_error_status=None):
        self.structured_output = structured_output
        self.is_error = is_error
        self.result = result
        self.stop_reason = stop_reason
        self.api_error_status = api_error_status
        self.subtype = "success"


def _install_fake_sdk(script):
    """Install a stub claude_agent_sdk module. `script` is a callable taking
    (prompt, options) and returning a list of messages, or raising."""
    import types as _types
    mod = _types.ModuleType("claude_agent_sdk")
    captured = {}

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    async def query(*, prompt, options=None, transport=None):
        captured["prompt"] = prompt
        captured["options"] = options
        for m in script(prompt, options):
            yield m

    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.AssistantMessage = _FakeAssistantMessage
    mod.ResultMessage = _FakeResultMessage
    mod.query = query
    sys.modules["claude_agent_sdk"] = mod
    return captured


def test_analyze():
    track = {"name": "T", "artist": "A", "album": "B"}
    good = {
        "one_liner": "A restless confession dressed up as a shrug.",
        "summary": "The narrator circles a relationship they can't name, "
                   "insisting they're fine while every line says otherwise.",
        "themes": ["avoidance", "self-deception", "late-night restlessness"],
        "mood": "hazy, defensive",
        "story": "It opens mid-argument and never resolves.",
        "context": "Recorded in a run of loose, low-stakes singles.",
        "key_lines": [{"line": "What am I doin'", "meaning": "The question "
                       "he keeps asking and never answers."}],
    }
    try:
        # --- normal case: structured_output comes back already parsed -------
        cap = _install_fake_sdk(
            lambda p, o: [_FakeAssistantMessage(), _FakeResultMessage(good)])
        got = S.analyze_song(track, "la la la", "claude-sonnet-5")
        assert got == good, got

        # request shape: no API key anywhere, schema drives the response
        o = cap["options"]
        assert o.model == "claude-sonnet-5"
        assert o.output_format["type"] == "json_schema"
        assert o.output_format["schema"] is S.ANALYSIS_SCHEMA
        assert "la la la" in cap["prompt"]
        # the sandbox must stay shut: lyrics are untrusted third-party text
        assert o.tools == [] and o.allowed_tools == []
        assert o.permission_mode == "dontAsk"
        assert o.setting_sources == []
        # A turn cap breaks this intermittently: the number of turns needed to
        # emit the full schema varies, so 1 failed nearly always and 4 still
        # failed ~2 runs in 3. There is no tool loop to bound here.
        assert getattr(o, "max_turns", None) is None, \
            f"max_turns={o.max_turns} will fail intermittently; leave it unset"

        # lyrics are truncated so a huge song can't blow the context
        S.analyze_song(track, "z" * 50000, "m")
        assert len(cap["prompt"]) < 12000, len(cap["prompt"])

        # --- is_error on a "success" subtype must still be treated as failure
        _install_fake_sdk(lambda p, o: [_FakeResultMessage(
            None, is_error=True, result="Bad Request: model not found")])
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisTransient:
            raise AssertionError("permanent error misclassified as transient")
        except S.AnalysisUnavailable:
            raise AssertionError("permanent error misclassified as unavailable")
        except Exception as ex:
            assert "model not found" in str(ex), ex

        # --- an expired login is surfaced as unavailable, not a retry loop --
        _install_fake_sdk(lambda p, o: [_FakeResultMessage(
            None, is_error=True,
            result="Failed to authenticate: OAuth session expired")])
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisUnavailable as ex:
            assert "claude" in str(ex).lower(), ex

        # --- rate limits are retryable -------------------------------------
        _install_fake_sdk(lambda p, o: [_FakeResultMessage(
            None, is_error=True, result="rate_limit exceeded",
            api_error_status=429)])
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisTransient:
            pass

        # --- schema-valid but empty of substance must not be cached --------
        for junk in (
            {**good, "one_liner": "Test", "summary": "Test"},
            {**good, "themes": ["a", "b"]},
            {**good, "summary": "It's a song."},          # too thin to be real
            {**good, "one_liner": "A song."},
        ):
            _install_fake_sdk(lambda p, o, j=junk: [_FakeResultMessage(j)])
            try:
                S.analyze_song(track, "l", "m")
                raise AssertionError(f"accepted placeholder output: {junk}")
            except S.AnalysisTransient:
                pass
        # ...and a genuine analysis of similar shape still passes
        _install_fake_sdk(lambda p, o: [_FakeResultMessage(good)])
        assert S.analyze_song(track, "l", "m") == good

        # --- missing structured output is retryable, not a crash -----------
        _install_fake_sdk(lambda p, o: [_FakeResultMessage(
            None, stop_reason="max_tokens")])
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisTransient as ex:
            assert "max_tokens" in str(ex), ex

        # --- SDK raising mid-stream is classified, not leaked --------------
        def explode(p, o):
            raise Exception("Connection reset by peer")
            yield  # pragma: no cover
        _install_fake_sdk(explode)
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisTransient:
            pass

        # --- the SDK wraps the real reason in a generic sentence. When there
        #     IS a reason, it must survive -- burying it under a login hint
        #     sends the user chasing a problem they don't have.
        def wrapped(p, o):
            raise Exception("Claude Code returned an error result: "
                            "Reached maximum number of turns (1)")
            yield  # pragma: no cover
        _install_fake_sdk(wrapped)
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisUnavailable as ex:
            raise AssertionError(f"real reason replaced by a login guess: {ex}")
        except Exception as ex:
            assert "maximum number of turns" in str(ex), ex

        # --- ...but when the CLI gives no reason at all ("success" is just its
        #     subtype leaking), fall back to something actionable ------------
        def opaque(p, o):
            raise Exception("Claude Code returned an error result: success")
            yield  # pragma: no cover
        _install_fake_sdk(opaque)
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisUnavailable as ex:
            assert "claude" in str(ex).lower(), ex
            assert "error result" not in str(ex), \
                "opaque SDK text leaked to the user instead of a fix"

        # --- SDK not installed at all --------------------------------------
        sys.modules["claude_agent_sdk"] = None  # forces ImportError
        try:
            S.analyze_song(track, "l", "m")
            raise AssertionError("should have raised")
        except S.AnalysisUnavailable as ex:
            assert "claude-agent-sdk" in str(ex), ex
    finally:
        sys.modules.pop("claude_agent_sdk", None)
    print("PASS  analyze_song via Claude Agent SDK")


def test_sdk_available():
    try:
        sys.modules["claude_agent_sdk"] = None
        assert S.sdk_available() is False
        _install_fake_sdk(lambda p, o: [])
        assert S.sdk_available() is True
    finally:
        sys.modules.pop("claude_agent_sdk", None)
    print("PASS  sdk_available detection")

def test_api_error_message():
    """An expired Claude Code login should read as an instruction, not a trace."""
    def boom(*a, **k):
        raise S.AnalysisUnavailable(S._AUTH_HINT)

    S.spotify.read_spotify = fake_read_spotify
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    S.analysis.analyze_song = boom
    S.analysis.sdk_available = lambda: True
    # fresh analysis cache so we exercise the live call, not the earlier result
    S.config.ANALYSIS_CACHE = S.Path("/tmp/slcache-err/analysis")
    _state["i"] = 0
    eng = spawn_engine("claude-sonnet-5")
    for _ in range(60):
        n = eng.now()
        if n.get("analysis_status") == "unavailable":
            break
        time.sleep(0.1)
    assert n["analysis_status"] == "unavailable", n
    det = eng.track_detail(FAKE_TRACKS[0]["id"])
    assert "claude" in det["analysis_error"].lower(), det["analysis_error"]
    assert det["lyrics"]["kind"] == "synced", "lyrics must still work"
    print("PASS  auth error surfaces cleanly and lyrics still render")


def test_page_statuses_match_backend():
    """Every analysis_status the engine can emit must be handled by the page,
    or the sidebar silently renders nothing."""
    emitted = {"waiting", "thinking", "researching", "ok", "context", "error",
               "skipped", "no_sdk", "unavailable"}
    html = S.PAGE_HTML
    for st in emitted:
        assert f"'{st}'" in html, f"page never handles analysis_status={st!r}"
    assert "no_key" not in html, "stale no_key branch left in the page"
    assert "ANTHROPIC_API_KEY" not in html, "stale API-key copy left in the page"
    print("PASS  page handles every backend analysis_status")


def test_run_agent_is_schema_agnostic():
    """_run_agent must not assume any particular schema's fields.

    It used to require a "one_liner" key -- a field only the two analysis
    schemas have -- so every valid translation was discarded as "no structured
    output", and the model was blamed for six debugging rounds.
    """
    payload = {"source_language": "Nigerian Pidgin", "already_english": "no",
               "lines": [{"line": "0", "en": "There's trouble", "note": "",
                          "confidence": "high"}]}
    _install_fake_sdk(lambda p, o: [_FakeResultMessage(payload)])
    try:
        got = S.analysis._run_agent("p", "sys", S.analysis.TRANSLATE_SCHEMA, "m")
        assert got == payload, got
    finally:
        sys.modules.pop("claude_agent_sdk", None)

    # ...but an empty object is still a failure worth retrying
    _install_fake_sdk(lambda p, o: [_FakeResultMessage({})])
    try:
        S.analysis._run_agent("p", "sys", S.analysis.TRANSLATE_SCHEMA, "m")
        raise AssertionError("accepted an empty object")
    except S.AnalysisTransient:
        pass
    finally:
        sys.modules.pop("claude_agent_sdk", None)
    print("PASS  _run_agent accepts any schema, rejects only empty output")


def test_translate_normalises():
    """String line numbers become ints; out-of-range rows are dropped rather
    than rendered under the wrong lyric."""
    lyr = {"lines": [{"t": None, "text": "Wahala dey o"},
                     {"t": None, "text": "I love you"}]}
    payload = {"source_language": "Nigerian Pidgin", "already_english": "no",
               "lines": [{"line": "0", "en": "There's trouble", "note": "n",
                          "confidence": "high"},
                         {"line": "99", "en": "nowhere", "note": "",
                          "confidence": "low"},
                         {"line": "notanumber", "en": "x", "note": "",
                          "confidence": "low"}]}
    _install_fake_sdk(lambda p, o: [_FakeResultMessage(payload)])
    try:
        out = S.analysis.translate_lyrics({"name": "x", "artist": "y"}, lyr, "m")
        assert out["needs_translation"] is True, out
        assert [l["i"] for l in out["lines"]] == [0], out["lines"]
        assert out["lines"][0]["en"] == "There's trouble"
    finally:
        sys.modules.pop("claude_agent_sdk", None)

    # an all-English song reports nothing to do
    _install_fake_sdk(lambda p, o: [_FakeResultMessage(
        {"source_language": "English", "already_english": "yes", "lines": []})])
    try:
        out = S.analysis.translate_lyrics({"name": "x", "artist": "y"}, lyr, "m")
        assert out["needs_translation"] is False, out
    finally:
        sys.modules.pop("claude_agent_sdk", None)
    print("PASS  translation indices normalised, bad rows dropped")


def test_feature_credits_and_other_edits():
    """Spotify titles carry feature credits LRCLIB usually doesn't index, and
    the nearest LRCLIB entry may be timed for a different edit."""
    import urllib.error, io
    calls = []
    synced = "[00:01.00]alpha\n[00:05.00]beta\n[00:09.00]gamma"
    track = {"name": "Song X (feat. A, B & C)", "artist": "Band Y",
             "album": "Album Z", "duration_ms": 184000, "id": "x"}
    assert S.lyrics._title_variants(track["name"]) == ["Song X (feat. A, B & C)", "Song X"]

    def responder(fn):
        def fake(url, headers=None, data=None, timeout=None):
            calls.append(url)
            r = fn(url)
            if isinstance(r, int):
                raise urllib.error.HTTPError(url, r, "x", {}, io.BytesIO(b"{}"))
            return r
        return fake

    orig_json, orig_text = S.net._http_json, S.net._http_text
    genius_hits = []
    S.net._http_text = lambda u, timeout=20: genius_hits.append(u) or json.dumps(
        {"response": {"sections": []}})
    try:
        # 1. only the bare title is indexed -> found, synced, via search
        bare_only = [{"id": 7, "artistName": "Band Y", "trackName": "Song X",
                      "duration": 180, "syncedLyrics": synced, "plainLyrics": "alpha"}]
        S.net._http_json = responder(lambda u: (bare_only if "search" in u and "feat" not in u
                                                else [] if "search" in u else 404))
        got = S.fetch_lyrics(track)
        assert got and got["kind"] == "synced" and got["lrclib_id"] == 7, got
        assert got["duration_delta"] == -4, got
        assert not genius_hits, "fell through to Genius despite an LRCLIB match"

        # 2. a 503 on the first exact lookup must not end the search early
        calls.clear()
        S.net._http_json = responder(lambda u: 503 if "/get" in u and "feat" in u
                                     else ({"id": 8, "artistName": "Band Y",
                                            "trackName": "Song X", "duration": 184,
                                            "syncedLyrics": synced} if "/get" in u else []))
        got = S.fetch_lyrics(track)
        assert got and got["lrclib_id"] == 8, (got, calls)

        # 3. right song, timed for an edit 34s longer -> words kept, timing
        #    dropped, and still preferred over Genius
        genius_hits.clear()
        other_edit = [{"id": 9, "artistName": "Band Y", "trackName": "Song X (feat. A, B & C)",
                       "duration": 218, "syncedLyrics": synced, "plainLyrics": None}]
        S.net._http_json = responder(lambda u: other_edit if "search" in u else 404)
        got = S.fetch_lyrics(track)
        assert got and got["kind"] == "plain" and got["timing_dropped"], got
        assert [l["text"] for l in got["lines"]] == ["alpha", "beta", "gamma"], got["lines"]
        assert not genius_hits, "Genius was preferred over LRCLIB's complete text"

        # 4. outside the sync window the title must match too
        genius_hits.clear()
        wrong_title = [{"id": 10, "artistName": "Band Y", "trackName": "Totally Different",
                        "duration": 218, "syncedLyrics": synced}]
        S.net._http_json = responder(lambda u: wrong_title if "search" in u else 404)
        assert S.fetch_lyrics(track) is None
        assert genius_hits, "should have fallen through to Genius"
    finally:
        S.net._http_json, S.net._http_text = orig_json, orig_text
    print("PASS  feature credits stripped; other-edit text beats a Genius fragment")


def test_stale_rules_protect_user_timing():
    V = S.config.LYRICS_SCHEMA_VERSION
    stale = S.lyrics.is_stale
    assert stale({"source": "LRCLIB"}) is True                      # v1
    assert stale({"source": "Genius", "v": 2}) is True              # truncated extractor
    assert stale({"source": "LRCLIB", "v": 2}) is False             # nothing wrong with it
    assert stale({"source": "Genius", "v": V}) is False
    assert stale(None) is False
    # tap-synced entries survive any bump, whatever version wrote them
    assert stale({"source": "Genius + your timing", "v": 2, "synced_locally": True}) is False
    assert stale({"source": "LRCLIB + your timing", "synced_locally": True}) is False
    print("PASS  cache staleness spares hand-synced lyrics")


def test_genius_nested_markup():
    """Genius nests divs inside lyric containers. A lazy regex stopped at the
    first nested </div> and kept 7 lines of a 36-line song."""
    page = ('<div data-lyrics-container="true" class="a">'
            '<div data-exclude-from-selection="true"><div>23 Contributors</div>'
            '<div>Translations</div>Song X Lyrics</div>'
            '[Verse]<br/>line one<br/><div class="ann">line two</div><br/>line three<br/>'
            '</div><div class="ad"></div>'
            '<div data-lyrics-container="true">line four<br/>'
            '<span><div>line five</div></span><br/>line six</div>')
    blocks = S.genius._lyric_blocks(page)
    assert len(blocks) == 2, blocks
    assert "line three" in blocks[0], "first container truncated at a nested div"
    assert "line six" in blocks[1], "second container truncated"

    search = {"response": {"sections": [{"type": "song", "hits": [{"result": {
        "primary_artist": {"name": "Band Y"}, "title": "Song X",
        "url": "https://genius.com/x"}}]}]}}
    orig = S.net._http_text
    S.net._http_text = lambda u, timeout=20: json.dumps(search) if "search" in u else page
    try:
        got = S.fetch_lyrics_genius({"name": "Song X", "artist": "Band Y", "album": ""})
    finally:
        S.net._http_text = orig
    texts = [l["text"] for l in got["lines"] if l["text"]]
    for want in ("line one", "line two", "line three", "line four", "line five", "line six"):
        assert want in texts, (want, texts)
    assert not any("Contributors" in t or "Translations" in t for t in texts), texts
    print("PASS  Genius extraction survives nested markup, drops its UI")



VOCAL_PAGE = (
    '<div data-lyrics-container="true">'
    '<div data-exclude-from-selection="true">12 Contributors<br/>Song Lyrics</div>'
    '[Intro: Singer B]<br/>alpha one<br/>alpha two<br/><br/>'
    '[Verse: Band A, <i>Band A &amp; Singer B</i>, <b>Singer B</b>]<br/>'
    'beta one<br/><i>gamma one<br/>gamma two</i><br/>'
    '<b>delta one<br/>delta two</b> (ooh)<br/><br/>'
    '[Chorus: Band A &amp; Singer B]<br/>epsilon one<br/><br/>'
    '[Verse 2]<br/>zeta one<br/>'
    '</div>')


def test_vocal_credits_parse():
    sung = S.genius.vocal_lines(S.genius.lyric_html(VOCAL_PAGE), "Band A", ["Band A"])
    who = {v["text"]: (v["singers"], v["credited"]) for v in sung}
    assert who["alpha one"] == (["Singer B"], True), who
    assert who["beta one"] == (["Band A"], True), who
    # italic runs across a line break -- state must carry over
    assert who["gamma one"] == (["Band A", "Singer B"], True), who
    assert who["gamma two"] == (["Band A", "Singer B"], True), who
    # bold line with a trailing plain ad-lib still belongs to the bold group
    assert who["delta two (ooh)"] == (["Singer B"], True), who
    assert who["epsilon one"] == (["Band A", "Singer B"], True), who
    # a label naming nobody falls back to the billed artist, marked uncredited
    assert who["zeta one"] == (["Band A"], False), who
    assert not any("Contributors" in v["text"] for v in sung), "Genius UI leaked"

    # names that contain a separator survive when they're the billed artist
    assert S.genius._split_names("Tyler, The Creator & Kali Uchis",
                                 ["Tyler, The Creator"]) == ["Tyler, The Creator", "Kali Uchis"]
    print("PASS  vocal credits: legend, multi-line formatting, fallbacks")


def test_vocal_alignment():
    sung = [{"text": "the first long line here", "singers": ["B"], "credited": True},
            {"text": "a much longer second line that one source splits", "singers": ["A"], "credited": True},
            {"text": "the chorus line goes like this", "singers": ["A", "B"], "credited": True}]
    shown = [{"text": "The first long line here"},
             {"text": "a much longer second line"},       # split in two
             {"text": "that one source splits"},
             {"text": "oh"},                              # ad-lib, matches nothing
             {"text": "The chorus line goes like this"},
             {"text": ""},
             {"text": "the chorus line goes like this"}]  # repeat written out
    per, cov = S.vocals.align(shown, sung)
    assert per[0] == ["B"] and per[1] == ["A"] and per[2] == ["A"], per
    assert per[3] == ["A"], "ad-lib should inherit the current singer"
    assert per[4] == ["A", "B"] and per[6] == ["A", "B"], per
    assert per[5] is None, "blank lines carry no singer"
    assert cov == 5 / 6, cov

    # a different song with the same title is refused rather than mislabelled
    S.genius.find_song = lambda t: ("A", "x", "https://genius.com/x")
    orig = S.net._http_text
    S.net._http_text = lambda u, timeout=20: VOCAL_PAGE
    try:
        unrelated = {"kind": "synced", "lines": [{"t": i, "text": f"nothing alike {i} here"}
                                                 for i in range(8)]}
        assert REAL_GET_VOCALS({"name": "Song", "artist": "Band A"}, unrelated) is None
        mine = {"kind": "synced", "lines": [{"t": 1, "text": "alpha one"},
                                            {"t": 2, "text": "beta one"},
                                            {"t": 3, "text": "gamma one"},
                                            {"t": 4, "text": "delta one"}]}
        v = REAL_GET_VOCALS({"name": "Song", "artist": "Band A"}, mine)
        assert v and v["credited"] and v["singers"] == ["Singer B", "Band A"], v
    finally:
        S.net._http_text = orig
        import importlib; importlib.reload(S.genius)
    print("PASS  alignment handles splits, repeats, ad-libs; refuses a different song")


def test_annotated_lyrics_and_prompt():
    ly = {"plain": "one\ntwo\nthree",
          "lines": [{"text": "one"}, {"text": "two"}, {"text": "three"}]}
    v = {"credited": True, "lines": [["Singer B"], ["Singer B"], ["Band A", "Singer B"]]}
    assert S.vocals.annotate(ly, v).splitlines() == [
        "[Singer B]", "one", "two", "[Band A & Singer B]", "three"]
    # nothing credited -> plain lyrics, no invented headings
    assert S.vocals.annotate(ly, {"credited": False, "lines": [["Band A"]] * 3}) == "one\ntwo\nthree"
    assert S.vocals.annotate(ly, None) == "one\ntwo\nthree"
    assert "Never infer anyone's gender" in S.analysis.SYSTEM_PROMPT
    assert "[A & B]" in S.analysis.ANALYSIS_PROMPT
    print("PASS  lyrics annotated by singer; prompt forbids guessing gender")


def test_engine_passes_credits_to_analysis():
    import shutil
    shutil.rmtree("/tmp/slcache-voc", ignore_errors=True)
    S.config.CACHE_DIR = S.Path("/tmp/slcache-voc")
    S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
    S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    S.spotify.read_spotify = fake_read_spotify
    S.lyrics.fetch_lyrics = fake_fetch_lyrics
    S.analysis.sdk_available = lambda: True
    _state["i"] = 0
    seen = {}

    def fake_vocals(track, lyrics):
        n = len(lyrics["lines"])
        return {"credited": True, "singers": ["Singer B"], "coverage": 1.0,
                "lines": [["Singer B"]] * n}

    def fake_analysis(track, lyrics_text, model):
        seen["text"] = lyrics_text
        return FAKE_ANALYSIS

    S.vocals.get_vocals = fake_vocals
    S.analysis.analyze_song = fake_analysis
    try:
        eng = spawn_engine()
        for _ in range(60):
            n = eng.now()
            if n.get("analysis_status") == "ok":
                break
            time.sleep(0.1)
        assert n["analysis_status"] == "ok", n
        assert n["has_vocals"] is True, n
        assert seen["text"].startswith("[Singer B]"), seen["text"][:40]
        det = eng.track_detail(FAKE_TRACKS[0]["id"])
        assert det["vocals"]["singers"] == ["Singer B"], det["vocals"]
        keys = [p.name for p in S.config.ANALYSIS_CACHE.glob("*.json")]
        assert any(k.endswith("_p2.json") for k in keys), keys
        assert any("vocals" in k for k in keys), "credits were not cached"
    finally:
        S.vocals.get_vocals = lambda track, lyrics: None
        S.analysis.analyze_song = fake_analyze
        S.config.CACHE_DIR = S.Path("/tmp/slcache")
        S.config.LYRICS_CACHE = S.config.CACHE_DIR / "lyrics"
        S.config.ANALYSIS_CACHE = S.config.CACHE_DIR / "analysis"
    print("PASS  engine fetches credits, annotates lyrics for the analysis, caches both")



if __name__ == "__main__":
    import shutil
    # Every cache dir any test touches. A stale one silently turns a test into
    # a cache-hit assertion, which is how this suite started passing without
    # exercising the code it claimed to.
    for d in ("/tmp/slcache", "/tmp/slcache-err", "/tmp/slcache-nosdk",
              "/tmp/slcache-retry", "/tmp/slcache-retry2", "/tmp/slcache-stale",
              "/tmp/slcache-ctx", "/tmp/slcache-ctx2", "/tmp/slcache-sync", "/tmp/slcache-voc"):
        shutil.rmtree(d, ignore_errors=True)
    test_lrc(); test_strip(); test_normalize(); test_applescript()
    test_artist_matching(); test_title_matching(); test_fetch_lyrics()
    test_genius_fallback(); test_genius_only_after_lrclib()
    test_feature_credits_and_other_edits(); test_stale_rules_protect_user_timing()
    test_genius_nested_markup()
    test_vocal_credits_parse(); test_vocal_alignment()
    test_annotated_lyrics_and_prompt()
    test_analyze(); test_sdk_available()
    test_stale_lyrics_cache_refetched()
    test_e2e()
    test_context_path_for_lyricless_tracks()
    test_context_degenerate_gate(); test_context_uses_websearch_only()
    test_sync_helpers(); test_sync_endpoints()
    test_run_agent_is_schema_agnostic(); test_translate_normalises()
    test_engine_passes_credits_to_analysis()
    test_no_sdk()
    test_retry_and_rerun(); test_track_eviction()
    test_api_error_message()
    test_page_statuses_match_backend()
    print("\nALL TESTS PASSED")
