"""Translate between the /audio/transcriptions wire and HiveMind's transcripts: its one name book.

The OpenAI-compatible transcription wire (`POST /audio/transcriptions`, relative to a base URL
that already ends in `/v1`) is spoken by hosted Whisper APIs and by local servers alike. A request
is a multipart form: the audio file, the model id, an optional language and the reply format; this
adapter always asks for `verbose_json`, whose reply carries time-stamped segments, and also accepts
the plain `json` reply (text only) a server without segments sends back. Codingrules section 8.6
makes this module the only place those wire field names appear: `request_form`/`request_files`
build the form, `transcript_from_json` turns either reply into a
`hivemind.llm.transcription.Transcript`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside
    `hivemind.llm.providers.openai_compat.transcription`. Called by this sub-package's
    `provider`; the HTTP mechanics live in its `client`. Calls into `hivemind.llm.errors`,
    `hivemind.llm.models` (for `JsonObject`) and `hivemind.llm.transcription` only.

Key invariants:
    - A caller's language hint always wins; a reply's language is kept only when it is a
      lowercase ISO 639 code (hosted APIs report an English name such as "english", which is not
      a code, so it becomes None rather than a guess).
    - A reply with text but no usable segments becomes one segment spanning the whole clip, so a
      caller always gets at least one time range for non-empty text; empty text is silence.
    - A malformed reply raises `MalformedOutputError` whose `raw` names only the reply's shape,
      never its text: a transcript is personal data and must not ride in an exception message.
      That includes a reply that breaks a transcript bound (say, runaway text): pydantic's own
      error would quote the words, so it is replaced, not chained.

See Also:
    - .claude/codingrules.md section 8.6 for "mapping.py is the only file where vendor field
      names appear".
    - docs/adr/0033-transcription-provider-whisper-first.md for verbose_json and the json fallback.
"""

from __future__ import annotations

import math
import re

from pydantic import JsonValue, ValidationError

from hivemind.llm.errors import MalformedOutputError
from hivemind.llm.models import JsonObject
from hivemind.llm.transcription import (
    LANGUAGE_PATTERN,
    AudioClip,
    Transcript,
    TranscriptSegment,
)

RESPONSE_FORMAT = "verbose_json"  # The reply shape that carries segments; plain json is accepted.
FILE_FIELD = "file"  # The multipart part holding the audio.
UPLOAD_STEM = "clip"  # A neutral filename stem; servers read the format from its extension.
_LANGUAGE_RE = re.compile(LANGUAGE_PATTERN)  # Screens a reply's language before it is kept.

# One multipart file part: (filename, content, media type), the shape httpx's `files=` takes.
FilePart = tuple[str, bytes, str]

__all__ = [
    "FILE_FIELD",
    "RESPONSE_FORMAT",
    "UPLOAD_STEM",
    "FilePart",
    "request_files",
    "request_form",
    "transcript_from_json",
]


def request_form(model: str, language: str | None) -> dict[str, str]:
    """Build the multipart form's text fields for one transcription.

    Args:
        model: The model id this provider instance serves.
        language: The caller's ISO 639 hint, or None to let the server detect it.

    Returns:
        The form fields, by the wire's own names; `language` is omitted when there is no hint.
    """
    form = {"model": model, "response_format": RESPONSE_FORMAT}
    if language is not None:
        form["language"] = language
    return form


def request_files(clip: AudioClip) -> dict[str, FilePart]:
    """Build the multipart form's one file part: the clip's bytes, named for their format.

    Args:
        clip: The audio to upload, in any accepted format (a device's Opus recording as much as
            a WAV).

    Returns:
        The file part, keyed by the wire's own field name. The filename's extension is the
        format's own (`clip.webm`, `clip.wav`): a Whisper-style server picks its decoder from it,
        so a fixed `.wav` would mislabel a compressed clip.
    """
    filename = f"{UPLOAD_STEM}.{clip.media_type.extension}"
    return {FILE_FIELD: (filename, clip.data, clip.media_type.value)}


def transcript_from_json(
    payload: JsonObject, *, clip: AudioClip, language: str | None, provider: str
) -> Transcript:
    """Build a Transcript from a `verbose_json` or plain `json` transcription reply.

    Args:
        payload: The parsed reply body.
        clip: The clip that was sent; its duration is the transcript's.
        language: The caller's hint, which wins over the reply's own language.
        provider: The manifest provider name, for a raised error.

    Returns:
        The transcript: the reply's text, and its segments (or one spanning the clip).

    Raises:
        MalformedOutputError: The reply has no string `text` field, or its content breaks a
            transcript bound (text past `MAX_TRANSCRIPT_CHARS`, for one).
    """
    text = payload.get("text")
    if not isinstance(text, str):
        raise MalformedOutputError(
            provider, raw=f"reply fields {sorted(payload)} include no string 'text'", attempts=1
        )
    try:
        return _transcript(payload, text, clip, language)
    except ValidationError as exc:
        # The validation error quotes the offending words; report only how many bounds broke,
        # and drop the cause (`from None`) so a logged traceback cannot carry them either.
        raw = f"reply broke {exc.error_count()} transcript bound(s)"
        raise MalformedOutputError(provider, raw=raw, attempts=1) from None


def _transcript(
    payload: JsonObject, text: str, clip: AudioClip, language: str | None
) -> Transcript:
    """Build the Transcript from a reply already known to carry string text.

    Raises:
        ValidationError: The reply's content breaks a transcript bound.
    """
    segments = tuple(_segments(payload.get("segments")))
    stripped = text.strip()
    # No usable segments but real text (a plain json reply): one segment spanning the clip.
    if not segments and stripped:
        segments = (TranscriptSegment(start_s=0.0, end_s=clip.duration_s, text=stripped),)
    return Transcript(
        text=stripped,
        language=language if language is not None else _code_or_none(payload.get("language")),
        duration_s=clip.duration_s,
        segments=tuple(sorted(segments, key=lambda segment: segment.start_s)),
    )


def _segments(raw: JsonValue) -> list[TranscriptSegment]:
    """Map the reply's `segments` array, skipping any entry without numeric times and text.

    A skipped entry loses only its timing: the reply's own `text` still carries every word.
    """
    if not isinstance(raw, list):
        return []
    mapped: list[TranscriptSegment] = []
    # Each entry either maps whole or is skipped; a partial segment would misplace its words.
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        start, end = _as_number(entry.get("start")), _as_number(entry.get("end"))
        text = entry.get("text")
        if start is None or end is None or not isinstance(text, str):
            continue
        start_s = max(start, 0.0)
        mapped.append(
            TranscriptSegment(
                start_s=start_s,
                end_s=max(end, start_s),
                text=text.strip(),
                confidence=_confidence(_as_number(entry.get("avg_logprob"))),
            )
        )
    return mapped


def _confidence(avg_logprob: float | None) -> float | None:
    """Turn a segment's mean token log-probability into a 0-to-1 confidence, or None if absent."""
    if avg_logprob is None:
        return None
    # A log-probability is never above zero; capping keeps a malformed value from overflowing.
    return math.exp(min(avg_logprob, 0.0))


def _as_number(value: JsonValue) -> float | None:
    """Return `value` as a float when it is a finite JSON number (never a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _code_or_none(value: JsonValue) -> str | None:
    """Return `value` when it is a lowercase ISO 639 code, None for a name or anything else."""
    return value if isinstance(value, str) and _LANGUAGE_RE.match(value) else None
