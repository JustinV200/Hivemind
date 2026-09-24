"""Tests for hivemind.llm.providers.anthropic.mapping: images and audio, in turns and tool results.

Split by feature (codingrules 14.2) from test_mapping.py: roadmap step 6.5 lets a tool result carry
media (a screenshot from `see`, a recording from `listen`), which this wire takes inside the
`tool_result` block itself for an image when the binding declares `vision`, and never for audio,
which the Messages API has no block for. The recorded fixture pins the whole wire shape.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/anthropic/mapping.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.anthropic.mapping for the module under test.
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
    LLMRequest,
    Message,
    Role,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
)
from hivemind.llm.providers.anthropic import mapping

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "anthropic"
FULL = ProviderCapabilities.full()
_IMAGE = ImagePart(media_type="image/png", data_base64="iVBORw0KGgo=")
_AUDIO = AudioPart(media_type="audio/wav", data_base64="UklGRg==")
_SEEN = "Captured the whole screen as a 4x4 image, attached."


def _see_round(result: ToolResultPart) -> LLMRequest:
    """A request replaying one `see` call and the result it got back."""
    call = ToolCall(id="toolu_see_1", name="see", arguments={})
    return make_request(
        model="test-model",
        messages=(
            Message(role=Role.ASSISTANT, parts=(ToolCallPart(call=call),)),
            Message(role=Role.USER, parts=(result,)),
        ),
    )


def _result(*media: ImagePart | AudioPart, content: str = _SEEN) -> ToolResultPart:
    return ToolResultPart(call_id="toolu_see_1", content=content, media=media)


def _recorded_messages(name: str) -> JsonValue:
    """Return the `messages` a recorded request fixture pins."""
    loaded: JsonValue = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded["messages"]


def test_a_tool_results_image_rides_inside_its_tool_result_block() -> None:
    body = mapping.to_create_params(_see_round(_result(_IMAGE)), FULL, provider="p")

    assert body["messages"] == _recorded_messages("request_tool_result_image.json")


def test_a_text_only_tool_result_keeps_its_content_a_plain_string() -> None:
    body = mapping.to_create_params(_see_round(_result()), FULL, provider="p")

    messages = body["messages"]
    assert isinstance(messages, list) and isinstance(messages[1], dict)
    assert messages[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_see_1", "content": _SEEN, "is_error": False}
    ]


def test_a_tool_result_with_no_text_sends_its_images_alone() -> None:
    body = mapping.to_create_params(_see_round(_result(_IMAGE, content="")), FULL, provider="p")

    block = _tool_result_block(body)
    assert block["content"] == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
        }
    ]


def test_a_tool_results_image_is_refused_without_vision() -> None:
    blind = FULL.model_copy(update={"vision": False})

    with pytest.raises(ProviderRequestError, match="vision"):
        mapping.to_create_params(_see_round(_result(_IMAGE)), blind, provider="p")


@pytest.mark.parametrize("capabilities", [FULL, FULL.model_copy(update={"audio": False})])
def test_audio_is_refused_in_a_tool_result_whatever_the_binding_declares(
    capabilities: ProviderCapabilities,
) -> None:
    with pytest.raises(ProviderRequestError, match="no audio"):
        mapping.to_create_params(_see_round(_result(_AUDIO)), capabilities, provider="p")


def test_an_audio_part_in_a_turn_is_refused_before_any_call() -> None:
    request = make_request(model="test-model", messages=(Message(role=Role.USER, parts=(_AUDIO,)),))

    with pytest.raises(ProviderRequestError, match="no audio"):
        mapping.to_create_params(request, FULL, provider="p")


def test_count_params_map_a_tool_results_image_the_same_way() -> None:
    params = mapping.to_count_params(_see_round(_result(_IMAGE)), FULL, provider="p")

    assert params["messages"] == _recorded_messages("request_tool_result_image.json")


def _tool_result_block(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the second message's one tool_result block, narrowed for assertions."""
    messages = body["messages"]
    assert isinstance(messages, list) and isinstance(messages[1], dict)
    content = messages[1]["content"]
    assert isinstance(content, list) and isinstance(content[0], dict)
    return content[0]
