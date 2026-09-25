"""Tests for hivemind.entrance.voice.models: the voice answer and the push-to-talk frames.

Every boundary model round-trips and refuses at least one malformed input (codingrules 14.3);
the answer carries exactly the outcome its intent names; a slice's bytes travel as base64 in
either alphabet a client may use.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from hivemind.brood_chamber import QuestionStatus, TaskStatus
from hivemind.entrance.models import AnsweredView, ChatAccepted
from hivemind.entrance.voice import (
    MAX_CHUNK_BYTES,
    AudioChunkFrame,
    AudioEndFrame,
    SpeakParams,
    VoiceAccepted,
    VoiceFrame,
    VoiceIntentKind,
    read_client_frame,
)
from waggle.clock import FakeClock
from waggle.ids import new_message_id, new_task_id

_CLOCK = FakeClock()
_ANSWERED = AnsweredView(
    question_id=new_message_id(_CLOCK),
    task_id=new_task_id(_CLOCK),
    task_status=TaskStatus.RUNNING,
    question_status=QuestionStatus.ANSWERED,
)
_BYTES = bytes(range(256)) * 4  # Every byte value, so both base64 alphabets differ from each other.


def _answer() -> VoiceAccepted:
    """A spoken answer's outcome."""
    return VoiceAccepted(
        intent=VoiceIntentKind.ANSWER,
        transcript="Lavender.",
        language="en",
        duration_s=1.0,
        answered=_ANSWERED,
    )


def test_a_voice_answer_round_trips_inside_its_frame() -> None:
    frame = VoiceFrame(result=_answer())

    assert VoiceFrame.model_validate_json(frame.model_dump_json()) == frame
    assert json.loads(frame.model_dump_json())["type"] == "voice"


def test_a_voice_answer_carries_exactly_the_outcome_its_intent_names() -> None:
    with pytest.raises(ValidationError, match="exactly its own outcome"):
        VoiceAccepted(
            intent=VoiceIntentKind.CHAT,
            transcript="hello",
            language=None,
            duration_s=1.0,
            answered=_ANSWERED,
        )
    both = _answer().model_dump() | {"chat": ChatAccepted(id="chat_x").model_dump()}
    with pytest.raises(ValidationError, match="exactly its own outcome"):
        VoiceAccepted.model_validate(both)


@pytest.mark.parametrize("encode", [base64.b64encode, base64.urlsafe_b64encode])
def test_a_slice_decodes_from_either_base64_alphabet(encode: Callable[[bytes], bytes]) -> None:
    text = json.dumps(
        {"type": "audio_chunk", "media_type": "audio/wav", "data": encode(_BYTES).decode()}
    )

    frame = read_client_frame(text)

    assert isinstance(frame, AudioChunkFrame) and frame.data == _BYTES


def test_an_end_frame_is_read_by_its_type() -> None:
    frame = read_client_frame(json.dumps({"type": "audio_end", "intent": "chat"}))

    assert isinstance(frame, AudioEndFrame) and (frame.intent, frame.language) == ("chat", None)


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "audio_end", "intent": "shout"},
        {"type": "audio_end", "intent": "chat", "extra": 1},
        {"type": "audio_chunk", "media_type": "audio/wav", "data": ""},
        {"type": "audio_chunk", "media_type": "audio/wav", "data": "AAAA", "duration_s": -1},
        {"type": "audio_blob", "data": "AAAA"},
    ],
)
def test_a_malformed_client_frame_is_refused(frame: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        read_client_frame(json.dumps(frame))


def test_a_slice_is_bounded_to_fit_one_socket_frame() -> None:
    too_big = base64.b64encode(b"\x00" * (MAX_CHUNK_BYTES + 1)).decode()

    with pytest.raises(ValidationError):
        read_client_frame(
            json.dumps({"type": "audio_chunk", "media_type": "audio/wav", "data": too_big})
        )


def test_the_route_query_takes_an_intent_and_refuses_strays() -> None:
    params = SpeakParams(intent="goal", duration_s=2.5, language="en-US")

    assert SpeakParams.model_validate(params.model_dump()) == params
    with pytest.raises(ValidationError):
        SpeakParams.model_validate({"intent": "goal", "budget_usd": 3})
    with pytest.raises(ValidationError):
        SpeakParams(intent="goal", duration_s=0.0)
