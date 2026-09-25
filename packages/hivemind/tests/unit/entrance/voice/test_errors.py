"""Tests for hivemind.entrance.voice.errors: each refusal's code and the status it is answered with.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import HiveMindError
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.gate import error_body, status_for
from hivemind.entrance.voice import errors
from hivemind.entrance.voice.errors import (
    AudioOverBudgetError,
    ClipRefusedError,
    InvalidIntentError,
    NothingHeardError,
    PushToTalkError,
    TranscriptionFailedError,
    TranscriptTooLongError,
    VoiceNotServedError,
)
from hivemind.llm.transcription import ClipProblem, InvalidAudioClipError


def test_every_voice_refusal_is_an_entrance_error_with_its_own_code() -> None:
    classes = [getattr(errors, name) for name in errors.__all__]

    codes = [cls.code for cls in classes]

    assert all(issubclass(cls, EntranceError) for cls in classes)
    assert len(codes) == len(set(codes))
    assert all(code.startswith("hivemind.entrance.") for code in codes)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InvalidIntentError("x"), 422),
        (ClipRefusedError(ClipProblem.TOO_LONG, "x"), 413),
        (ClipRefusedError(ClipProblem.TOO_LARGE, "x"), 413),
        (ClipRefusedError(ClipProblem.UNSUPPORTED_FORMAT, "x"), 415),
        (ClipRefusedError(ClipProblem.MISSING_DURATION, "x"), 422),
        (ClipRefusedError(ClipProblem.MALFORMED, "x"), 422),
        (AudioOverBudgetError(), 429),
        (TranscriptionFailedError(), 503),
        (NothingHeardError(), 422),
        (TranscriptTooLongError(2, 1), 422),
        (PushToTalkError("x"), 422),
        (VoiceNotServedError(), 404),
    ],
)
def test_each_refusal_is_answered_with_its_status(error: HiveMindError, status: int) -> None:
    assert status_for(error) == status
    assert error_body(error) == (status, error_body(error)[1])
    assert error_body(error)[1].error == error.code


def test_the_budget_refusal_speaks_of_audio_not_requests() -> None:
    assert "audio budget" in str(AudioOverBudgetError())


def test_a_clip_refusal_from_the_boundary_keeps_its_problem_and_detail() -> None:
    boundary = InvalidAudioClipError(ClipProblem.UNSUPPORTED_FORMAT, "audio/flac is not accepted")

    refused = ClipRefusedError.from_invalid(boundary)

    assert (refused.problem, refused.http_status) == (ClipProblem.UNSUPPORTED_FORMAT, 415)
    assert str(refused) == "Audio clip refused (UNSUPPORTED_FORMAT): audio/flac is not accepted."
