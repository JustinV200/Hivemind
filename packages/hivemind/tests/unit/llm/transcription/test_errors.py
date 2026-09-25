"""Tests for hivemind.llm.transcription.errors: the transcription boundary's refusals.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.errors for the module under test.
"""

from __future__ import annotations

from hivemind.common.errors import HiveMindError
from hivemind.llm import TranscriptionUnsupportedError
from hivemind.llm.errors import LLMError
from hivemind.llm.transcription import ClipProblem, InvalidAudioClipError


def test_a_refused_clip_is_not_an_llm_error_so_no_fallback_catches_it() -> None:
    error = InvalidAudioClipError(ClipProblem.TOO_LARGE, "30000000 bytes exceeds the limit")

    assert isinstance(error, HiveMindError)
    assert not isinstance(error, LLMError)
    assert error.problem is ClipProblem.TOO_LARGE
    assert "TOO_LARGE" in str(error)


def test_an_unsupported_binding_names_the_provider_its_kind_and_the_fix() -> None:
    # The registry raises it (hivemind.llm.registry.config) when the slot is bound, before a clip.
    error = TranscriptionUnsupportedError("hosted", "anthropic")

    assert isinstance(error, LLMError)
    assert (error.provider, error.kind) == ("hosted", "anthropic")
    assert "kind='anthropic', which cannot transcribe" in str(error)
    assert "'whisper_local' or 'openai_compat'" in str(error)


def test_every_transcription_error_has_its_own_code() -> None:
    codes = {InvalidAudioClipError.code, TranscriptionUnsupportedError.code, LLMError.code}

    assert len(codes) == 3
