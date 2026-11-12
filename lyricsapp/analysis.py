"""Everything that talks to Claude, through the local Claude Code install.

Two paths: read the lyrics and explain the song, or -- when no lyrics exist
anywhere -- research the song and the artist with web search instead.
"""

from __future__ import annotations

import asyncio

# --------------------------------------------------------------------------
# Claude -- what is this song about
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a thoughtful music writer. You explain what songs are actually "
    "about: specific, grounded in the lyrics in front of you, no vague filler. "
    "When a song leans on slang, dialect, regional references, or cultural "
    "shorthand a listener might not catch, you decode it plainly. When you are "
    "not confident about a background fact, you say so rather than inventing it.\n\n"
    "Be careful about who is speaking. A song billed to one artist is often "
    "sung by several, and the person telling the story is a character, not "
    "necessarily the artist. Never infer anyone's gender -- a singer's or the "
    "narrator's -- from a name, a stage name, or the artist billing. Refer to "
    "singers by name, and to the narrator as 'the narrator' or 'they', unless "
    "the lyrics themselves make the narrator's gender explicit."
)

ANALYSIS_PROMPT = """Song: {name}
Artist: {artist}
Album: {album}

Lyrics:
---
{lyrics}
---

Where a line in square brackets names someone -- [Caroline Polachek], or
[A & B] when they sing together -- the lines beneath it, up to the next such
heading, are sung by them. These come from the song's published vocal credits.
Use them: when the voice changes, say so, and don't merge different singers
into one narrator.

Explain what this song is about."""

# Drives structured output. The CLI enforces this schema, so the response comes
# back already parsed -- no prose to strip, no JSON to repair.
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "one_liner": {
            "type": "string",
            "description": "a single vivid sentence capturing what the song is",
        },
        "summary": {
            "type": "string",
            "description": "2-4 sentences on what happens in the song and what it means",
        },
        "themes": {
            "type": "array",
            "description": "3 to 6 short theme tags",
            "items": {"type": "string"},
        },
        "mood": {
            "type": "string",
            "description": "a few words describing the emotional register",
        },
        "genre": {
            "type": "string",
            "description": "the genre or subgenre, specific rather than broad "
                           "(e.g. 'Afro-fusion / street-pop' not just 'pop'). "
                           "Omit this field entirely if you are unsure.",
        },
        "story": {
            "type": "string",
            "description": "a paragraph walking through how the song unfolds, verse to verse",
        },
        "context": {
            "type": "string",
            "description": "a paragraph on the artist, the era, and any cultural or "
                           "musical background that makes the song land differently "
                           "once you know it",
        },
        "key_lines": {
            "type": "array",
            "description": "2 to 5 memorable lines worth decoding",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "string",
                             "description": "an exact line from the lyrics"},
                    "meaning": {"type": "string",
                                "description": "what it means and why it matters"},
                },
                "required": ["line", "meaning"],
                "additionalProperties": False,
            },
        },
    },
    # Only the three fields the sidebar leads with are required. Demanding all
    # seven (including the nested key_lines objects) made the CLI fail
    # validation outright on some songs -- "Failed to provide valid structured
    # output after 5 attempts", or worse, a schema-shaped answer full of
    # placeholders. The page renders every optional field defensively, so a
    # response missing `story` is a slightly thinner panel, not a broken one.
    "required": ["one_liner", "summary", "themes"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# The no-lyrics path: research the song instead of reading it
# --------------------------------------------------------------------------

# Roughly 1 in 5 tracks has no lyrics in any database -- recent releases that
# volunteers haven't transcribed yet, instrumentals, and genuinely obscure
# records. Those used to leave the panel empty. This fills it with what is
# actually documented about the song and the artist.
CONTEXT_SYSTEM_PROMPT = (
    "You research music. You look things up before answering, and you never "
    "invent facts about an artist or a release. Small independent artists are "
    "exactly the case where guessing does damage: if searching turns up little, "
    "report the little you found and set confidence honestly. Saying 'almost "
    "nothing is documented about this' is a useful answer. Making something up "
    "is not.\n\n"
    "Don't infer anyone's gender from their name or stage name. Refer to people "
    "by name, or use the pronouns their own published biographies use."
)

CONTEXT_PROMPT = """Research this song. Search for it before answering.

Song: {name}
Artist: {artist}
Album: {album}

There are no lyrics available for it, so cover what is documented instead: who
the artist is and where they come from, the scene and genre they sit in, and
anything known about this particular release -- when it came out, who worked on
it, what it was written about, how it was received.

If this is a small or new artist and there is little coverage, say what you did
find and be explicit about what you couldn't confirm."""

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "one_liner": {
            "type": "string",
            "description": "a single sentence placing this song and artist",
        },
        "genre": {
            "type": "string",
            "description": "the genre or subgenre, specific rather than broad "
                           "(e.g. 'alternative R&B / neo-soul' not just 'R&B'). "
                           "Use 'Unknown' if you could not establish it.",
        },
        "artist_background": {
            "type": "string",
            "description": "who the artist is: where they're from, how they "
                           "started, what scene they belong to, what else "
                           "they're known for",
        },
        "song_context": {
            "type": "string",
            "description": "what is documented about this release specifically "
                           "-- date, label, collaborators, what it's about, "
                           "reception. Say so if little is documented.",
        },
        "sounds_like": {
            "type": "array",
            "description": "2-4 artists or reference points a listener would "
                           "recognise, for placing the sound",
            "items": {"type": "string"},
        },
        "confidence": {
            "type": "string",
            "description": "how much you could actually verify: 'high' if well "
                           "documented, 'some' if thin but real, 'none' if you "
                           "found nothing reliable",
            "enum": ["high", "some", "none"],
        },
    },
    "required": ["one_liner", "genre", "artist_background", "confidence"],
    "additionalProperties": False,
}


class AnalysisUnavailable(Exception):
    """The Claude Agent SDK isn't installed, or Claude Code isn't logged in."""


class AnalysisTransient(Exception):
    """Rate limit, overload, or network blip -- worth retrying."""


def sdk_available():
    """True if claude_agent_sdk imports. Cheap enough to call per request."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


# Errors the SDK reports that are worth another attempt. Anything else (bad
# request, auth failure) will fail identically on retry, so we surface it.
_TRANSIENT_ERRORS = {"rate_limit", "server_error"}
_TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504, 529}


def _run_agent(prompt, system_prompt, schema, model, tools=()):
    """Run one structured-output turn through the local Claude Code install.

    Returns the parsed object. Raises AnalysisUnavailable if the SDK is missing
    or Claude Code isn't authenticated, AnalysisTransient for failures worth
    retrying, and plain Exception for everything else.
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage, ClaudeAgentOptions, ResultMessage, query,
        )
    except ImportError as ex:
        raise AnalysisUnavailable(
            "claude-agent-sdk is not installed (pip3 install claude-agent-sdk)"
        ) from ex

    tools = list(tools)
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        output_format={"type": "json_schema", "schema": schema},
        # Default is no tools at all: song lyrics are untrusted text from a
        # public database, and with an empty toolset they can't talk this into
        # anything but writing an answer. The research path opts into
        # WebSearch specifically -- never bash or file access.
        tools=tools,
        allowed_tools=tools,
        permission_mode="dontAsk",
        # No max_turns. It looks like cheap insurance and isn't: producing the
        # full schema takes a variable number of turns, so any cap fails
        # intermittently -- 1 failed almost always, 4 still failed ~2 runs in 3,
        # and the error ("Reached maximum number of turns") reads like a
        # runaway rather than a config mistake. With tools=[] there is no tool
        # loop to run away, and ANALYSIS_ATTEMPTS already bounds retries.
        # Don't inherit the user's CLAUDE.md, settings, or project context:
        # this should behave identically no matter where the script is run.
        setting_sources=[],
    )

    async def _run():
        result = None
        text_seen = False
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                text_seen = True
            elif isinstance(message, ResultMessage):
                result = message
        return result, text_seen

    try:
        result, _ = asyncio.run(_run())
    except Exception as ex:
        # The SDK raises a bare Exception for an error result rather than a
        # typed one, so classify on the message text.
        detail = _unwrap_sdk_error(str(ex))
        if detail is None:
            # The CLI gave no reason at all (its subtype leaks through as
            # "success"). An expired login is the most common cause, so lead
            # with that rather than echoing a string nobody can act on.
            raise AnalysisUnavailable(_AUTH_HINT + _OPAQUE_SUFFIX) from ex
        if _looks_transient(detail):
            raise AnalysisTransient(detail) from ex
        if _looks_like_auth(detail):
            raise AnalysisUnavailable(_AUTH_HINT) from ex
        raise Exception(detail) from ex

    if result is None:
        raise AnalysisTransient("Claude Code exited without returning a result")

    # An error can arrive as is_error=True on a subtype of "success", so this
    # has to be checked explicitly -- it is not implied by subtype.
    if result.is_error:
        detail = (result.result or "").strip() or "unknown error"
        status = getattr(result, "api_error_status", None)
        if status in _TRANSIENT_STATUSES or _looks_transient(detail):
            raise AnalysisTransient(detail)
        if _looks_like_auth(detail):
            raise AnalysisUnavailable(_AUTH_HINT)
        raise Exception(detail)

    parsed = result.structured_output
    # Only check that something schema-shaped came back. Per-caller validation
    # belongs to the caller: this used to demand a "one_liner" key, which is a
    # field only the two analysis schemas have -- so every valid translation
    # was thrown away here and reported as "no structured output".
    if not isinstance(parsed, dict) or not parsed:
        raise AnalysisTransient(
            "Claude returned no structured output"
            + (f" (stop reason: {result.stop_reason})" if result.stop_reason else "")
        )
    return parsed


def analyze_song(track, lyrics_text, model):
    """Read the lyrics and explain what the song is about."""
    prompt = ANALYSIS_PROMPT.format(
        name=track["name"], artist=track["artist"], album=track["album"],
        lyrics=lyrics_text[:8000],
    )
    analysis = _run_agent(prompt, SYSTEM_PROMPT, ANALYSIS_SCHEMA, model)
    if not analysis.get("one_liner"):
        raise AnalysisTransient("analysis came back without a one_liner")
    # The schema guarantees the shape, not the substance -- a run occasionally
    # comes back schema-valid but filled with placeholders ("Test", themes
    # ["a","b"]). Caching that would mean the user is stuck with it forever, so
    # treat it as a failed attempt and let the retry loop have another go.
    if _looks_degenerate(analysis):
        raise AnalysisTransient("Claude returned placeholder text, not an analysis")
    return analysis


def analyze_song_context(track, model):
    """No lyrics to read, so research the song and the artist instead.

    This is the path for recent releases nobody has transcribed yet, for
    instrumentals, and for small artists -- which is also where a model is most
    tempted to invent. WebSearch is enabled so answers come from what's
    actually published, and `confidence` records how much could be verified.
    """
    prompt = CONTEXT_PROMPT.format(
        name=track["name"], artist=track["artist"],
        album=track.get("album") or "(unknown)",
    )
    context = _run_agent(prompt, CONTEXT_SYSTEM_PROMPT, CONTEXT_SCHEMA, model,
                         tools=["WebSearch"])
    if not context.get("one_liner"):
        raise AnalysisTransient("research came back without a one_liner")
    # "Found nothing" is a legitimate, useful answer here, so a short response
    # is only degenerate when the model also claims it found something.
    if context.get("confidence") != "none" and _looks_degenerate_context(context):
        raise AnalysisTransient("Claude returned placeholder text, not research")
    context["kind"] = "context"
    return context


def _looks_degenerate_context(c):
    one = str(c.get("one_liner", "")).strip()
    bg = str(c.get("artist_background", "")).strip()
    if one.lower() in _PLACEHOLDERS or bg.lower() in _PLACEHOLDERS:
        return True
    return len(one) < 25 or len(bg) < 60


_PLACEHOLDERS = {"test", "testing", "example", "n/a", "na", "none", "tbd",
                 "placeholder", "string", "foo", "bar", "lorem ipsum"}


def _looks_degenerate(a):
    """True if the response satisfies the schema but says nothing."""
    one, summary = str(a.get("one_liner", "")), str(a.get("summary", ""))
    if one.strip().lower() in _PLACEHOLDERS or summary.strip().lower() in _PLACEHOLDERS:
        return True
    # A real one-liner is a sentence; a real summary is 2-4 of them.
    if len(one.strip()) < 25 or len(summary.strip()) < 60:
        return True
    themes = [str(t).strip() for t in (a.get("themes") or [])]
    if len(themes) < 2 or any(len(t) < 3 for t in themes):
        return True
    return False


_AUTH_HINT = ("Claude Code isn't logged in. Run `claude` in a terminal and "
              "sign in, then play the song again.")

# The SDK wraps the CLI's reason inside a generic sentence. Sometimes the reason
# is real ("Reached maximum number of turns"), sometimes it's just the CLI's
# subtype leaking through ("success"), which tells the user nothing.
_OPAQUE_PREFIX = "Claude Code returned an error result:"
_OPAQUE_SUFFIX = (" (If you are already signed in, run `claude -p hello` to see "
                  "the real error -- the SDK doesn't pass it through here.)")


def _unwrap_sdk_error(msg):
    """Pull the CLI's actual reason back out of the SDK's wrapper message.

    Returns the reason, or None when there genuinely isn't one. Returning the
    wrapper verbatim would bury the real cause; substituting a guess for a
    reason we *do* have would send the user chasing the wrong problem.
    """
    idx = msg.find(_OPAQUE_PREFIX)
    if idx == -1:
        return msg
    detail = msg[idx + len(_OPAQUE_PREFIX):].strip()
    return detail or None if detail.lower() != "success" else None


def _looks_transient(msg):
    m = msg.lower()
    if any(e in m for e in _TRANSIENT_ERRORS):
        return True
    return any(w in m for w in
               ("rate limit", "overloaded", "timed out", "timeout",
                "temporarily", "connection", "try again",
                # The CLI exhausting its own structured-output retries is
                # song-specific and usually clears on another attempt.
                "structured output"))


def _looks_like_auth(msg):
    m = msg.lower()
    return any(w in m for w in
               ("authenticate", "oauth", "not logged in", "unauthorized",
                "invalid api key", "credit balance", "billing"))




# --------------------------------------------------------------------------
# Translation
# ------------------------------------------------------------------------
TRANSLATE_SYSTEM_PROMPT = (
    "You translate song lyrics for a listener who wants to follow along. You "
    "work line by line and you are honest about doubt: dialect, regional slang, "
    "and coined words are exactly where a confident translation misleads people. "
    "Look terms up rather than guessing. When a line could plausibly mean more "
    "than one thing, give your best reading and mark it low confidence -- a "
    "flagged uncertain line is useful, a wrong line stated plainly is not."
)

TRANSLATE_PROMPT = """Translate this song's lyrics into English for someone
listening along.

Song: {name}
Artist: {artist}

The lines are numbered. Return only the lines that a English-speaking listener
would not already understand -- skip lines that are already plain English.
If the whole song is already in English, set needs_translation to false and
return no lines.

Search for any slang, dialect, or regional reference you are not certain of
before translating it.

{numbered}"""

# Deliberately strings only, and objects whose properties are all strings.
# That is the exact shape ANALYSIS_SCHEMA and CONTEXT_SCHEMA use, and it is the
# only shape observed to come back reliably: a version of this schema using a
# boolean and an integer line index failed every single attempt with
# "no structured output (stop reason: tool_use)". The line index is therefore a
# string that the caller parses.
TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "source_language": {
            "type": "string",
            "description": "the language(s) the lyrics are in, e.g. "
                           "'Nigerian Pidgin with Yoruba' or 'English'",
        },
        "already_english": {
            "type": "string",
            "description": "'yes' if the whole song is already plain English "
                           "and needs no translation, otherwise 'no'",
            "enum": ["yes", "no"],
        },
        "lines": {
            "type": "array",
            "description": "only the lines needing translation, in order",
            "items": {
                "type": "object",
                "properties": {
                    "line": {
                        "type": "string",
                        "description": "the line number exactly as given in the "
                                       "prompt, as digits",
                    },
                    "en": {
                        "type": "string",
                        "description": "the line's meaning in plain English",
                    },
                    "note": {
                        "type": "string",
                        "description": "the slang or reference being decoded, "
                                       "if it needs saying. Empty string if not.",
                    },
                    "confidence": {
                        "type": "string",
                        "description": "'high' if sure, 'some' if the reading is "
                                       "reasonable but contested, 'low' if guessing",
                        "enum": ["high", "some", "low"],
                    },
                },
                "required": ["line", "en", "note", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["source_language", "already_english", "lines"],
    "additionalProperties": False,
}


# A very long song would push both latency and output size up sharply, and the
# tail of a lyric sheet is usually repeated chorus anyway.
MAX_TRANSLATE_LINES = 120


def translate_lyrics(track, lyrics, model):
    """Translate the lines a listener wouldn't already follow.

    Returns {source_language, needs_translation, lines: [{i, en, note?,
    confidence}]}. Lines are keyed by index into `lyrics["lines"]`, so the page
    can show each translation under the line it belongs to.
    """
    lines = lyrics.get("lines") or []
    numbered = "\n".join(f"{i}: {l.get('text', '')}"
                         for i, l in enumerate(lines[:MAX_TRANSLATE_LINES])
                         if (l.get("text") or "").strip())
    if not numbered.strip():
        return {"source_language": "unknown", "needs_translation": False,
                "lines": []}

    prompt = TRANSLATE_PROMPT.format(
        name=track["name"], artist=track["artist"], numbered=numbered)
    out = _run_agent(prompt, TRANSLATE_SYSTEM_PROMPT, TRANSLATE_SCHEMA, model,
                     tools=["WebSearch"])

    # Normalise back to what the page wants: an int index, and a boolean.
    clean = []
    for item in out.get("lines") or []:
        try:
            i = int(str(item.get("line", "")).strip())
        except ValueError:
            continue
        # Drop anything pointing outside the lyric sheet rather than letting
        # the page render a translation under the wrong line.
        if 0 <= i < len(lines) and item.get("en"):
            clean.append({"i": i, "en": item["en"],
                          "note": (item.get("note") or "").strip(),
                          "confidence": item.get("confidence") or "some"})
    return {
        "source_language": out.get("source_language") or "unknown",
        "needs_translation": (out.get("already_english") != "yes") and bool(clean),
        "lines": clean,
    }
