"""Tests for hivemind.llm.transcription.capabilities: the declaration and the shared guard.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/capabilities.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.capabilities for the module under test.
"""

from __future__ import annotations

import pytest
from builders.audio import make_silence_clip
from pydantic import ValidationError

from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.transcription.capabilities import (
    BAD_LANGUAGE_STATUS_CODE,
    CLIP_TOO_LONG_STATUS_CODE,
    DEFAULT_MAX_CLIP_S,
    TranscriptionCapabilities,
    check_request,
)


def test_transcription_capabilities_default_to_the_plainest_honest_shape() -> None:
    capabilities = TranscriptionCapabilities()

    assert capabilities.streaming is False
    assert capabilities.word_timestamps is False
    assert capabilities.max_clip_s == DEFAULT_MAX_CLIP_S
    assert capabilities.languages is None


def test_transcription_capabilities_reject_a_malformed_language_code() -> None:
    with pytest.raises(ValidationError):
        TranscriptionCapabilities(languages=("en", "English"))


def test_transcription_capabilities_reject_a_non_positive_clip_ceiling() -> None:
    with pytest.raises(ValidationError):
        TranscriptionCapabilities(max_clip_s=0)


def test_transcription_capabilities_round_trip_through_json() -> None:
    capabilities = TranscriptionCapabilities(streaming=True, max_clip_s=30.0, languages=("en",))

    restored = TranscriptionCapabilities.model_validate_json(capabilities.model_dump_json())

    assert restored == capabilities


def test_check_request_accepts_a_clip_within_the_ceiling_and_no_language() -> None:
    check_request("p", TranscriptionCapabilities(), make_silence_clip(1.0), None)


def test_check_request_refuses_a_clip_past_max_clip_s_with_413() -> None:
    capabilities = TranscriptionCapabilities(max_clip_s=0.5)

    with pytest.raises(ProviderRequestError) as caught:
        check_request("p", capabilities, make_silence_clip(1.0), None)

    assert caught.value.status_code == CLIP_TOO_LONG_STATUS_CODE
    assert caught.value.error_type == "clip_too_long"
    assert caught.value.provider == "p"


def test_check_request_refuses_a_malformed_language_hint_with_400() -> None:
    with pytest.raises(ProviderRequestError) as caught:
        check_request("p", TranscriptionCapabilities(), make_silence_clip(), "English")

    assert caught.value.status_code == BAD_LANGUAGE_STATUS_CODE
    assert caught.value.error_type == "invalid_language"


def test_check_request_refuses_a_language_outside_the_declared_set() -> None:
    capabilities = TranscriptionCapabilities(languages=("en", "de"))

    with pytest.raises(ProviderRequestError) as caught:
        check_request("p", capabilities, make_silence_clip(), "fr")

    assert caught.value.error_type == "unsupported_language"


def test_check_request_accepts_a_declared_language() -> None:
    capabilities = TranscriptionCapabilities(languages=("en", "de"))

    check_request("p", capabilities, make_silence_clip(), "de")
