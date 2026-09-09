"""Tests for hivemind.llm.providers.anthropic.mapping: request/response wire translation.

Every test builds an `LLMRequest`/`anthropic.types.Message` directly (no network, no SDK client)
and asserts on the JSON-serialisable dict `to_create_params`/`to_count_params` return, or on the
`LLMResponse` `from_message` returns. Snapshot-style dict comparisons, per codingrules 14.3.

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

import anthropic.types as at
import pytest
from builders.llm import make_request
from pydantic import JsonValue

from hivemind.forage.slots import Effort
from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import (
    ImagePart,
    JsonObject,
    LLMRequest,
    Message,
    Role,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)
from hivemind.llm.providers.anthropic import mapping

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "anthropic"
FULL = ProviderCapabilities.full()
NONE = ProviderCapabilities.none()


def _request(**overrides: object) -> LLMRequest:
    fields: dict[str, object] = {"model": "test-model"}
    fields.update(overrides)
    return make_request(**fields)


def _load_message(name: str) -> at.Message:
    return at.Message.model_validate(json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8")))


def _as_dict(value: JsonValue) -> JsonObject:
    """Narrow a parsed JsonValue to an object, for assertions on a mapped request body."""
    assert isinstance(value, dict)
    return value


def _as_list(value: JsonValue) -> list[JsonValue]:
    """Narrow a parsed JsonValue to an array, for assertions on a mapped request body."""
    assert isinstance(value, list)
    return value


def _message_content(body: JsonObject, index: int) -> list[JsonValue]:
    """Return `body["messages"][index]["content"]`, narrowed for indexing in assertions."""
    message = _as_dict(_as_list(body["messages"])[index])
    return _as_list(message["content"])


def _tools(body: JsonObject) -> list[JsonValue]:
    """Return `body["tools"]`, narrowed for indexing in assertions."""
    return _as_list(body["tools"])


# ──────────────────────────────────────────────────────────────────────────────
# to_create_params: model, max_tokens, messages
# ──────────────────────────────────────────────────────────────────────────────


def test_to_create_params_uses_request_model_and_max_tokens() -> None:
    body = mapping.to_create_params(_request(max_output_tokens=555), FULL, provider="p")

    assert body["model"] == "test-model"
    assert body["max_tokens"] == 555


def test_to_create_params_raises_when_model_is_none() -> None:
    with pytest.raises(ProviderRequestError):
        mapping.to_create_params(_request(model=None), FULL, provider="p")


def test_to_create_params_wraps_system_with_a_cache_breakpoint() -> None:
    body = mapping.to_create_params(_request(system="be nice"), FULL, provider="p")

    assert body["system"] == [
        {"type": "text", "text": "be nice", "cache_control": {"type": "ephemeral"}}
    ]


def test_to_create_params_omits_system_when_absent() -> None:
    body = mapping.to_create_params(_request(), FULL, provider="p")

    assert "system" not in body


# ──────────────────────────────────────────────────────────────────────────────
# Content blocks: text, tool result, tool call, image
# ──────────────────────────────────────────────────────────────────────────────


def test_to_create_params_maps_text_and_tool_result_parts_on_one_wire_message() -> None:
    msg = Message(
        role=Role.USER, parts=(TextPart(text="hi"), ToolResultPart(call_id="c1", content="ok"))
    )

    body = mapping.to_create_params(_request(messages=(msg,)), FULL, provider="p")

    content = _message_content(body, 0)
    assert content[0] == {"type": "text", "text": "hi"}
    assert content[1] == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "ok",
        "is_error": False,
    }


def test_to_create_params_maps_an_assistant_tool_call_part() -> None:
    call = ToolCall(id="call_1", name="test_tool", arguments={"x": 1})
    msg = Message(role=Role.ASSISTANT, parts=(ToolCallPart(call=call),))

    body = mapping.to_create_params(_request(messages=(msg,)), FULL, provider="p")

    block = _message_content(body, 0)[0]
    assert block == {"type": "tool_use", "id": "call_1", "name": "test_tool", "input": {"x": 1}}


def test_to_create_params_maps_an_image_when_vision_is_declared() -> None:
    msg = Message(role=Role.USER, parts=(ImagePart(media_type="image/png", data_base64="AAAA"),))

    body = mapping.to_create_params(_request(messages=(msg,)), FULL, provider="p")

    block = _message_content(body, 0)[0]
    assert block == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }


def test_to_create_params_raises_on_an_image_when_vision_is_false() -> None:
    msg = Message(role=Role.USER, parts=(ImagePart(media_type="image/png", data_base64="AAAA"),))
    capabilities = FULL.model_copy(update={"vision": False})

    with pytest.raises(ProviderRequestError):
        mapping.to_create_params(_request(messages=(msg,)), capabilities, provider="p")


# ──────────────────────────────────────────────────────────────────────────────
# Tools: strict, additionalProperties/required, tool_choice, capability gating
# ──────────────────────────────────────────────────────────────────────────────


def test_to_create_params_adds_strict_and_the_missing_schema_fields() -> None:
    tool = ToolDefinition(
        name="test_tool", description="d", parameters={"type": "object", "properties": {}}
    )

    body = mapping.to_create_params(_request(tools=(tool,)), FULL, provider="p")

    wire_tool = _as_dict(_tools(body)[0])
    schema = _as_dict(wire_tool["input_schema"])
    assert wire_tool["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == []
    assert body["tool_choice"] == {"type": "auto"}


def test_to_create_params_keeps_a_schemas_own_additional_properties_and_required() -> None:
    tool = ToolDefinition(
        name="test_tool",
        description="d",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": True,
            "required": ["a"],
        },
    )

    body = mapping.to_create_params(_request(tools=(tool,)), FULL, provider="p")

    wire_tool = _as_dict(_tools(body)[0])
    schema = _as_dict(wire_tool["input_schema"])
    assert schema["additionalProperties"] is True
    assert schema["required"] == ["a"]


def test_to_create_params_omits_tools_when_native_tool_calls_is_false() -> None:
    tool = ToolDefinition(
        name="test_tool", description="d", parameters={"type": "object", "properties": {}}
    )
    capabilities = FULL.model_copy(update={"native_tool_calls": False})

    body = mapping.to_create_params(_request(tools=(tool,)), capabilities, provider="p")

    assert "tools" not in body
    assert "tool_choice" not in body


def test_to_create_params_omits_tool_choice_when_no_tools_are_present() -> None:
    body = mapping.to_create_params(_request(), FULL, provider="p")

    assert "tool_choice" not in body


# ──────────────────────────────────────────────────────────────────────────────
# thinking / output_config.effort / structured output / temperature / stop_sequences
# ──────────────────────────────────────────────────────────────────────────────


def test_to_create_params_sends_thinking_only_when_reasoning_control() -> None:
    body = mapping.to_create_params(_request(), FULL, provider="p")
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}

    reduced = FULL.model_copy(update={"reasoning_control": False})
    body_reduced = mapping.to_create_params(_request(), reduced, provider="p")
    assert "thinking" not in body_reduced


def test_to_create_params_sends_effort_only_when_reasoning_control() -> None:
    body = mapping.to_create_params(_request(effort=Effort.HIGH), FULL, provider="p")
    assert _as_dict(body["output_config"])["effort"] == "high"

    reduced = FULL.model_copy(update={"reasoning_control": False})
    body_reduced = mapping.to_create_params(_request(), reduced, provider="p")
    assert "output_config" not in body_reduced


def test_to_create_params_sends_structured_output_only_when_schema_output() -> None:
    schema = {"type": "object", "properties": {}}
    body = mapping.to_create_params(_request(response_schema=schema), FULL, provider="p")
    assert _as_dict(body["output_config"])["format"] == {"type": "json_schema", "schema": schema}

    reduced = FULL.model_copy(update={"schema_output": False})
    body_reduced = mapping.to_create_params(_request(response_schema=schema), reduced, provider="p")
    # reasoning_control is still True on `reduced`, so output_config still carries "effort" --
    # only its "format" key is gated by schema_output.
    assert "format" not in _as_dict(body_reduced["output_config"])


def test_to_create_params_sends_temperature_only_when_set() -> None:
    body = mapping.to_create_params(_request(), FULL, provider="p")
    assert "temperature" not in body

    body_set = mapping.to_create_params(_request(temperature=0.5), FULL, provider="p")
    assert body_set["temperature"] == 0.5


def test_to_create_params_sends_stop_sequences_only_when_set() -> None:
    body = mapping.to_create_params(_request(), FULL, provider="p")
    assert "stop_sequences" not in body

    body_set = mapping.to_create_params(_request(stop_sequences=("END",)), FULL, provider="p")
    assert body_set["stop_sequences"] == ["END"]


def test_to_create_params_omits_every_optional_field_at_zero_capabilities() -> None:
    tool = ToolDefinition(
        name="test_tool", description="d", parameters={"type": "object", "properties": {}}
    )

    body = mapping.to_create_params(_request(tools=(tool,), system="s"), NONE, provider="p")

    assert set(body.keys()) == {"model", "max_tokens", "messages", "system"}


# ──────────────────────────────────────────────────────────────────────────────
# to_count_params
# ──────────────────────────────────────────────────────────────────────────────


def test_to_count_params_has_no_max_tokens_or_thinking_or_output_config() -> None:
    body = mapping.to_count_params(_request(), FULL, provider="p")

    assert "max_tokens" not in body
    assert "thinking" not in body
    assert "output_config" not in body
    assert body["model"] == "test-model"


def test_to_count_params_raises_when_model_is_none() -> None:
    with pytest.raises(ProviderRequestError):
        mapping.to_count_params(_request(model=None), FULL, provider="p")


def test_to_count_params_includes_tools_when_native_tool_calls_is_declared() -> None:
    tool = ToolDefinition(
        name="test_tool", description="d", parameters={"type": "object", "properties": {}}
    )

    body = mapping.to_count_params(_request(tools=(tool,)), FULL, provider="p")

    assert _as_dict(_tools(body)[0])["name"] == "test_tool"


# ──────────────────────────────────────────────────────────────────────────────
# from_message: text, tool calls, refusal, thinking, usage, every stop reason
# ──────────────────────────────────────────────────────────────────────────────


def test_from_message_maps_text_and_usage() -> None:
    message = _load_message("message_text.json")

    response = mapping.from_message(message, provider="p")

    assert response.text == "Hello there."
    assert response.stop_reason == StopReason.END_TURN
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 4
    assert response.usage.cached_tokens == 2
    assert response.model == "test-model"


def test_from_message_maps_a_tool_call() -> None:
    message = _load_message("message_tool_call.json")

    response = mapping.from_message(message, provider="p")

    assert response.stop_reason == StopReason.TOOL_USE
    assert response.tool_calls[0].name == "get_weather"
    assert response.tool_calls[0].arguments == {"city": "Paris"}


def test_from_message_returns_refusal_instead_of_raising() -> None:
    message = _load_message("message_refusal.json")

    response = mapping.from_message(message, provider="p")

    assert response.stop_reason == StopReason.REFUSAL


def test_from_message_maps_a_thinking_block_to_reasoning_summary() -> None:
    message = _load_message("message_thinking.json")

    response = mapping.from_message(message, provider="p")

    assert response.reasoning_summary == "Consider the options."
    assert response.text == "Answer."


def test_from_message_tool_call_preserves_nested_argument_structure() -> None:
    message = at.Message(
        id="m",
        type="message",
        role="assistant",
        model="test-model",
        content=[
            at.ToolUseBlock(
                type="tool_use", id="t1", name="test_tool", input={"nested": {"a": [1, 2, 3]}}
            )
        ],
        stop_reason="tool_use",
        usage=at.Usage(input_tokens=1, output_tokens=1),
    )

    response = mapping.from_message(message, provider="p")

    assert response.tool_calls[0].arguments == {"nested": {"a": [1, 2, 3]}}


def test_from_message_usage_defaults_cached_tokens_to_zero_when_absent() -> None:
    message = at.Message(
        id="m",
        type="message",
        role="assistant",
        model="test-model",
        content=[at.TextBlock(type="text", text="hi")],
        stop_reason="end_turn",
        usage=at.Usage(input_tokens=1, output_tokens=1),
    )

    response = mapping.from_message(message, provider="p")

    assert response.usage.cached_tokens == 0


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("end_turn", StopReason.END_TURN),
        ("max_tokens", StopReason.MAX_TOKENS),
        ("tool_use", StopReason.TOOL_USE),
        ("stop_sequence", StopReason.STOP_SEQUENCE),
        ("refusal", StopReason.REFUSAL),
        ("pause_turn", StopReason.END_TURN),
        ("model_context_window_exceeded", StopReason.MAX_TOKENS),
    ],
)
def test_from_message_maps_every_stop_reason(wire: str, expected: StopReason) -> None:
    message = at.Message(
        id="m",
        type="message",
        role="assistant",
        model="test-model",
        content=[],
        stop_reason=wire,
        usage=at.Usage(input_tokens=1, output_tokens=1),
    )

    response = mapping.from_message(message, provider="p")

    assert response.stop_reason == expected
