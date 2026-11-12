"""The state machine: polls Spotify, resolves lyrics, drives analysis."""

from __future__ import annotations

import random
import sys
import threading
import urllib.error
import time

from . import analysis, config, lyrics as lyrics_mod, spotify, sync
from . import vocals as vocals_mod
from .analysis import AnalysisTransient, AnalysisUnavailable
from .cache import cache_read, cache_write

# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------


# How many finished tracks to hold in memory. Entries carry full lyrics plus an
# analysis, so an unbounded dict grows for as long as the script runs -- which
# for this app is "all day". Disk cache means eviction costs nothing but a
# reread.
MAX_TRACKS = 200

# Transient failures get this many attempts before the track is marked errored.
ANALYSIS_ATTEMPTS = 3


class Engine:
    def __init__(self, model, offline_analysis=False):
        self.model = model
        self.lock = threading.Lock()
        self.spotify = {"status": "starting"}
        self.position = 0.0
        self.playing = False
        self.polled_at = time.monotonic()
        self.current_id = None
        # track_id -> {track, lyrics, lyrics_status, analysis, analysis_status}
        # Ordered: insertion order drives eviction.
        self.tracks = {}
        self.workers = set()
        self.offline_analysis = offline_analysis
        self._stopped = threading.Event()
        # Probed once at startup so the UI can explain itself before any track
        # resolves; the per-request check in _work_inner is what actually gates.
        self.has_sdk = analysis.sdk_available()

    # -- polling ---------------------------------------------------------

    def stop(self):
        """Ask the poll loop to exit. Also wakes it immediately."""
        self._stopped.set()

    def poll_forever(self):
        while not self._stopped.is_set():
            info = spotify.read_spotify()
            now = time.monotonic()
            with self.lock:
                self.spotify = info
                if info.get("status") == "ok":
                    self.position = info["position"]
                    self.playing = info["playing"]
                    self.polled_at = now
                    tid = info["track"]["id"]
                    if tid != self.current_id:
                        self.current_id = tid
                        self._ensure_entry(info["track"])
                else:
                    self.playing = False
            # Event.wait rather than sleep so stop() takes effect at once
            # instead of after up to a full poll interval.
            self._stopped.wait(config.POLL_INTERVAL)

    def _ensure_entry(self, track):
        """Called with lock held. Kicks off lyric + analysis fetches per track.

        Replaying a track that previously failed re-runs it: an expired login or
        a rate limit is worth another shot once the user comes back around,
        and before this the first failure stuck for the life of the process.
        """
        tid = track["id"]
        entry = self.tracks.get(tid)
        if entry is not None:
            entry["track"] = track
            retryable = entry["analysis_status"] in ("error", "unavailable")
            if not (retryable and tid not in self.workers):
                return
        else:
            self._evict_if_needed()
            self.tracks[tid] = {
                "track": track,
                "lyrics": None,
                "lyrics_status": "loading",
                "analysis": None,
                "analysis_status": "waiting",
                "analysis_error": None,
                "vocals": None,
            }
        if tid not in self.workers:
            self.workers.add(tid)
            threading.Thread(target=self._work, args=(tid, track), daemon=True).start()

    def _evict_if_needed(self):
        """Called with lock held. Drop the oldest entries that aren't in flight."""
        while len(self.tracks) >= MAX_TRACKS:
            for tid in list(self.tracks):
                if tid not in self.workers and tid != self.current_id:
                    del self.tracks[tid]
                    break
            else:
                return  # everything is busy; let it ride rather than spin

    # -- background work -------------------------------------------------

    def _set(self, tid, **fields):
        with self.lock:
            entry = self.tracks.get(tid)
            if entry is not None:
                entry.update(fields)
            return entry is not None

    def _work(self, tid, track):
        try:
            self._work_inner(tid, track)
        finally:
            with self.lock:
                self.workers.discard(tid)

    def _work_inner(self, tid, track):
        key = f"{track['artist']} - {track['name']} - {track['album']}"

        lyrics = cache_read(config.LYRICS_CACHE, key)
        if lyrics_mod.is_stale(lyrics):
            # Written by a version with a known matching or extraction bug, so
            # it may be wrong or truncated. Refetch rather than trust it.
            lyrics = None
        if lyrics is None:
            try:
                lyrics = lyrics_mod.fetch_lyrics(track)
            except Exception as e:
                lyrics = None
                print(f"[lyrics] error for {key}: {e}", file=sys.stderr)
            if lyrics is not None:
                cache_write(config.LYRICS_CACHE, key, lyrics)

        if not self._set(tid, lyrics=lyrics,
                         lyrics_status="ok" if lyrics else "not_found"):
            return

        # No words to read: research the song instead of leaving a dead panel.
        # Instrumentals count -- who made it and why is still the question the
        # user opened this for.
        researching = lyrics is None or lyrics.get("kind") == "instrumental"

        vocals = None if researching else self._vocals_for(track, key, lyrics)
        if vocals:
            self._set(tid, vocals=vocals)

        if researching:
            cache_key = f"{key}|{self.model}|context"
        else:
            # Keyed on the matched lyrics too: an analysis written from a wrong
            # match must not survive the corrected one.
            # "p2": prompts gained vocalist credits and a rule against
            # inferring anyone's gender, so analyses written before that --
            # which assumed the narrator from the artist billing -- are redone.
            cache_key = f"{key}|{self.model}|{lyrics.get('lrclib_id')}|p2"
        done_status = "context" if researching else "ok"

        cached = cache_read(config.ANALYSIS_CACHE, cache_key)
        if cached:
            self._set(tid, analysis=cached, analysis_status=done_status)
            return

        if not analysis.sdk_available():
            self._set(tid, analysis_status="no_sdk")
            return

        self._set(tid, analysis_status="researching" if researching else "thinking",
                  analysis_error=None)

        lyrics_text = "" if researching else vocals_mod.annotate(lyrics, vocals)
        for attempt in range(1, ANALYSIS_ATTEMPTS + 1):
            try:
                result = (analysis.analyze_song_context(track, self.model) if researching
                          else analysis.analyze_song(track, lyrics_text, self.model))
            except AnalysisUnavailable as ex:
                # Not retryable in-process: needs the user to install or log in.
                print(f"[claude] {ex}", file=sys.stderr)
                self._set(tid, analysis_status="unavailable",
                          analysis_error=str(ex))
                return
            except AnalysisTransient as ex:
                if attempt == ANALYSIS_ATTEMPTS:
                    print(f"[claude] giving up after {attempt} attempts: {ex}",
                          file=sys.stderr)
                    self._set(tid, analysis_status="error",
                              analysis_error=str(ex))
                    return
                # Exponential backoff, jittered so a burst of track changes
                # doesn't retry in lockstep.
                delay = (2 ** (attempt - 1)) * 1.5 + random.uniform(0, 0.75)
                print(f"[claude] attempt {attempt} failed ({ex}); "
                      f"retrying in {delay:.1f}s", file=sys.stderr)
                time.sleep(delay)
            except Exception as ex:
                print(f"[claude] {ex}", file=sys.stderr)
                self._set(tid, analysis_status="error", analysis_error=str(ex))
                return
            else:
                cache_write(config.ANALYSIS_CACHE, cache_key, result)
                self._set(tid, analysis=result, analysis_status=done_status)
                return

    def _vocals_for(self, track, key, lyrics):
        """Who sings each on-screen line, from Genius credits. Cached, including
        the "nothing usable" answer, so a song without credits isn't looked up
        again on every play. Never raises: lyrics and analysis go ahead without
        it."""
        vkey = f"{key}|vocals|{lyrics.get('lrclib_id') or lyrics.get('source')}|v1"
        cached = cache_read(config.ANALYSIS_CACHE, vkey)
        if cached is not None:
            return cached.get("vocals")
        try:
            vocals = vocals_mod.get_vocals(track, lyrics)
        except Exception as ex:
            print(f"[vocals] {ex}", file=sys.stderr)
            return None   # transient: don't cache, try again next play
        cache_write(config.ANALYSIS_CACHE, vkey, {"vocals": vocals})
        return vocals

    # -- read models -----------------------------------------------------

    def now(self):
        with self.lock:
            info = dict(self.spotify)
            status = info.get("status")
            payload = {"status": status, "has_sdk": self.has_sdk}
            if status == "error":
                payload["message"] = info.get("message")
            if status != "ok":
                return payload
            elapsed = time.monotonic() - self.polled_at if self.playing else 0.0
            dur = info["track"]["duration_ms"] / 1000.0
            payload.update({
                "playing": self.playing,
                "position": min(self.position + elapsed, dur),
                "track": info["track"],
            })
            entry = self.tracks.get(info["track"]["id"], {})
            payload["lyrics_status"] = entry.get("lyrics_status", "loading")
            payload["analysis_status"] = entry.get("analysis_status", "waiting")
            payload["has_vocals"] = bool((entry.get("vocals") or {}).get("credited"))
            return payload

    # -- sync mode -------------------------------------------------------

    def save_sync(self, tid, stamps):
        """Store user-tapped timing and start highlighting with it right away."""
        with self.lock:
            entry = self.tracks.get(tid)
            if entry is None:
                return {"ok": False, "error": "unknown track"}
            track, lyrics = entry["track"], entry["lyrics"]
        if not lyrics:
            return {"ok": False, "error": "no lyrics loaded for this track"}
        try:
            synced = sync.apply_sync(lyrics, stamps)
        except (ValueError, KeyError, TypeError) as ex:
            return {"ok": False, "error": str(ex)}

        key = f"{track['artist']} - {track['name']} - {track['album']}"
        cache_write(config.LYRICS_CACHE, key, synced)
        self._set(tid, lyrics=synced, lyrics_status="ok")
        timed = sum(1 for l in synced["lines"] if l.get("t") is not None)
        return {"ok": True, "timed": timed, "total": len(synced["lines"])}

    def translate(self, tid):
        """Translate this track's lyrics, cached per track+model."""
        with self.lock:
            entry = self.tracks.get(tid)
            if entry is None:
                return {"ok": False, "error": "unknown track"}
            track, lyrics = entry["track"], entry["lyrics"]
        if not lyrics or not lyrics.get("lines"):
            return {"ok": False, "error": "no lyrics to translate"}

        key = (f"{track['artist']} - {track['name']} - {track['album']}"
               f"|{self.model}|translation|{lyrics.get('lrclib_id')}")
        cached = cache_read(config.ANALYSIS_CACHE, key)
        if cached:
            return {"ok": True, **cached}
        if not analysis.sdk_available():
            return {"ok": False, "error": "claude-agent-sdk is not installed"}
        try:
            out = analysis.translate_lyrics(track, lyrics, self.model)
        except AnalysisUnavailable as ex:
            return {"ok": False, "error": str(ex)}
        except Exception as ex:
            print(f"[claude] translation failed: {ex}", file=sys.stderr)
            return {"ok": False, "error": str(ex)}
        cache_write(config.ANALYSIS_CACHE, key, out)
        return {"ok": True, **out}

    def publish_sync(self, tid):
        """Send this track's timing to LRCLIB. Only ever from an explicit action."""
        with self.lock:
            entry = self.tracks.get(tid)
            if entry is None:
                return {"ok": False, "error": "unknown track"}
            track, lyrics = entry["track"], entry["lyrics"]
        if not lyrics or lyrics.get("kind") != "synced":
            return {"ok": False, "error": "nothing synced to publish"}
        try:
            status = sync.publish_to_lrclib(track, lyrics)
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode("utf-8", "replace")[:200] or f"HTTP {ex.code}"
            return {"ok": False, "error": detail}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}
        return {"ok": True, "status": status}

    def track_detail(self, tid):
        with self.lock:
            entry = self.tracks.get(tid)
            if not entry:
                return {"status": "unknown"}
            return {
                "status": "ok",
                "track": entry["track"],
                "lyrics": entry["lyrics"],
                "lyrics_status": entry["lyrics_status"],
                "analysis": entry["analysis"],
                "analysis_status": entry["analysis_status"],
                "analysis_error": entry["analysis_error"],
                "vocals": entry.get("vocals"),
            }


