"""Tests for hivemind.llm.providers.openai_compat.media: images and audio on the chat wire.

Driven through `mapping.request_to_json`, the one caller, so what is asserted is the whole body a
server would receive: a tool message stays a plain string, and the media of a turn's tool results
rides in one user message right after the tool messages, labelled by call. The recorded fixture
pins that whole shape.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/media.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.media for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders.llm import make_request
from pydantic import JsonValue

from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import (
    AudioPart,
    ImagePart,
    JsonObject,
    Message,
    Role,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
)
from hivemind.llm.providers.openai_compat import mapping
from hivemind.llm.providers.openai_compat.media import media_turn

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "openai_compat"
FULL = ProviderCapabilities.full()
_IMAGE = ImagePart(media_type="image/png", data_base64="iVBORw0KGgo=")
_AUDIO = AudioPart(media_type="audio/wav", data_base64="UklGRg==")
_SEEN = ToolResultPart(
    call_id="call_see",
    content="Captured the whole screen as a 4x4 image, attached.",
    media=(_IMAGE,),
)
_HEARD = ToolResultPart(call_id="call_listen", content="Recorded 0.5s, attached.", media=(_AUDIO,))


def _body(*results: ToolResultPart, capabilities: ProviderCapabilities = FULL) -> JsonObject:
    """Map a round in which the model called see and listen and got `results` back."""
    calls = (
        ToolCall(id="call_see", name="see", arguments={}),
        ToolCall(id="call_listen", name="listen", arguments={"seconds": 0.5}),
    )
    request = make_request(
        messages=(
            Message(role=Role.ASSISTANT, parts=tuple(ToolCallPart(call=c) for c in calls)),
            Message(role=Role.USER, parts=results),
        )
    )
    return mapping.request_to_json(
        request, model="local-small", capabilities=capabilities, provider="p"
    )


def _recorded_messages(name: str) -> JsonValue:
    """Return the `messages` a recorded request fixture pins."""
    loaded: JsonValue = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded["messages"]


def test_tool_messages_stay_strings_and_one_user_message_carries_their_media() -> None:
    body = _body(_SEEN, _HEARD)

    assert body["messages"] == _recorded_messages("request_tool_result_media.json")


def test_a_text_only_tool_round_adds_no_user_message() -> None:
    plain = ToolResultPart(call_id="call_see", content="nothing to show")

    messages = _body(plain)["messages"]

    assert isinstance(messages, list)
    assert [message["role"] for message in messages if isinstance(message, dict)] == [
        "assistant",
        "tool",
    ]


def test_a_tool_results_image_is_refused_without_vision() -> None:
    with pytest.raises(ProviderRequestError, match="vision"):
        _body(_SEEN, capabilities=FULL.model_copy(update={"vision": False}))


def test_a_tool_results_audio_is_refused_without_audio() -> None:
    with pytest.raises(ProviderRequestError, match="audio=False"):
        _body(_HEARD, capabilities=FULL.model_copy(update={"audio": False}))


def test_audio_in_a_container_input_audio_cannot_name_is_refused() -> None:
    ogg = ToolResultPart(
        call_id="call_listen",
        content="x",
        media=(AudioPart(media_type="audio/ogg", data_base64="T2dn"),),
    )

    with pytest.raises(ProviderRequestError, match="wav or mp3"):
        _body(ogg)


def test_an_audio_part_in_a_turn_becomes_an_input_audio_content_part() -> None:
    request = make_request(messages=(Message(role=Role.USER, parts=(_AUDIO,)),))

    body = mapping.request_to_json(request, model="local-small", capabilities=FULL, provider="p")

    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": "UklGRg==", "format": "wav"}}
            ],
        }
    ]


def test_media_turn_is_none_when_no_result_carries_media() -> None:
    assert media_turn((ToolResultPart(call_id="c", content="x"),), FULL, provider="p") is None
