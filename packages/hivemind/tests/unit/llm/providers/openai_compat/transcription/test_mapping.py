"""Tests for hivemind.llm.providers.openai_compat.transcription.mapping: the wire's name book.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/transcription/mapping.py (codingrules
    section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.transcription.mapping for the module under test.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from builders.audio import make_tone_clip
from pydantic import JsonValue

from hivemind.llm.errors import MalformedOutputError
from hivemind.llm.models import JsonObject
from hivemind.llm.providers.openai_compat.transcription.mapping import (
    FILE_FIELD,
    RESPONSE_FORMAT,
    UPLOAD_FILENAME,
    request_files,
    request_form,
    transcript_from_json,
)
from hivemind.llm.transcription.models import MAX_TRANSCRIPT_CHARS

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "fixtures" / "llm" / "openai_compat"


def _fixture(name: str) -> JsonObject:
    """Load one recorded reply body."""
    loaded: JsonValue = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_request_form_asks_for_verbose_json_and_names_the_model() -> None:
    assert request_form("speech-model", None) == {
        "model": "speech-model",
        "response_format": RESPONSE_FORMAT,
    }


def test_request_form_carries_a_language_hint_only_when_given() -> None:
    assert request_form("speech-model", "en")["language"] == "en"


def test_request_files_uploads_the_wav_bytes_under_the_file_field() -> None:
    clip = make_tone_clip(0.1)

    assert request_files(clip) == {FILE_FIELD: (UPLOAD_FILENAME, clip.data, "audio/wav")}


def test_transcript_from_a_verbose_reply_keeps_its_segments_and_trims_the_text() -> None:
    clip = make_tone_clip(1.0)

    transcript = transcript_from_json(
        _fixture("transcription_verbose.json"), clip=clip, language=None, provider="p"
    )

    assert transcript.text == "Hello from the hive."
    assert [segment.text for segment in transcript.segments] == ["Hello from", "the hive."]
    assert [segment.start_s for segment in transcript.segments] == [0.0, 0.48]
    assert transcript.segments[0].confidence == pytest.approx(math.exp(-0.21))
    assert transcript.duration_s == clip.duration_s


def test_transcript_from_a_verbose_reply_drops_a_language_name_that_is_not_a_code() -> None:
    # The recorded hosted reply says "english": a name, not an ISO 639 code.
    transcript = transcript_from_json(
        _fixture("transcription_verbose.json"), clip=make_tone_clip(), language=None, provider="p"
    )

    assert transcript.language is None


def test_transcript_prefers_the_callers_language_hint() -> None:
    transcript = transcript_from_json(
        _fixture("transcription_verbose.json"), clip=make_tone_clip(), language="en", provider="p"
    )

    assert transcript.language == "en"


def test_transcript_keeps_a_reported_language_code() -> None:
    payload: JsonObject = {"text": "hallo", "language": "de"}

    transcript = transcript_from_json(payload, clip=make_tone_clip(), language=None, provider="p")

    assert transcript.language == "de"


def test_transcript_from_a_plain_json_reply_is_one_segment_spanning_the_clip() -> None:
    clip = make_tone_clip(1.0)

    transcript = transcript_from_json(
        _fixture("transcription_text.json"), clip=clip, language=None, provider="p"
    )

    assert transcript.text == "Hello from the hive."
    assert len(transcript.segments) == 1
    segment = transcript.segments[0]
    assert (segment.start_s, segment.end_s, segment.confidence) == (0.0, clip.duration_s, None)


def test_transcript_of_empty_text_is_silence() -> None:
    transcript = transcript_from_json(
        {"text": "  ", "segments": []}, clip=make_tone_clip(), language=None, provider="p"
    )

    assert transcript.text == ""
    assert transcript.segments == ()


def test_transcript_skips_a_segment_without_numeric_times_and_clamps_the_rest() -> None:
    payload: JsonObject = {
        "text": "a b",
        "segments": [
            {"start": "soon", "end": 0.4, "text": "a"},
            {"start": -0.5, "end": -0.6, "text": " b", "avg_logprob": 3.0},
            "not a segment",
            {"start": True, "end": 1, "text": "bool is not a number"},
        ],
    }

    transcript = transcript_from_json(payload, clip=make_tone_clip(), language=None, provider="p")

    assert [segment.text for segment in transcript.segments] == ["b"]
    segment = transcript.segments[0]
    assert (segment.start_s, segment.end_s, segment.confidence) == (0.0, 0.0, 1.0)


def test_a_reply_past_a_transcript_bound_is_malformed_and_never_quotes_it() -> None:
    runaway = "private " * (MAX_TRANSCRIPT_CHARS // 8 + 1)

    with pytest.raises(MalformedOutputError) as caught:
        transcript_from_json({"text": runaway}, clip=make_tone_clip(), language=None, provider="p")

    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_reply_without_text_is_malformed_and_never_quotes_its_body() -> None:
    payload: JsonObject = {"transcript": "private words", "segments": []}

    with pytest.raises(MalformedOutputError) as caught:
        transcript_from_json(payload, clip=make_tone_clip(), language=None, provider="p")

    assert "private words" not in str(caught.value)
    assert "private words" not in caught.value.raw
