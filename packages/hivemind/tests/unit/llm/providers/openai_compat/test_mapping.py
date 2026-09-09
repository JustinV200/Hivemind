"""Tests for hivemind.llm.providers.openai_compat.mapping: the wire translation, in isolation.

No httpx and no OpenAICompatProvider here: every test calls `request_to_json`,
`response_from_json` or `StreamState` directly, so a mapping bug is caught without also needing a
working HTTP layer.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/mapping.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.mapping for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders.llm import make_request, make_tool
from pydantic import JsonValue

from hivemind.forage.slots import Effort
from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import MalformedOutputError, ProviderRequestError
from hivemind.llm.models import (
    ImagePart,
    JsonObject,
    LLMChunk,
    Message,
    Role,
    StopReason,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
)
from hivemind.llm.providers.openai_compat import mapping

# Path(__file__)-relative, importlib-free (the phase 3 brief permits this for adapter fixtures).
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llm" / "openai_compat"


def _load_fixture(name: str) -> JsonObject:
    payload: JsonValue = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _wire_message(body: JsonObject, index: int) -> JsonObject:
    """Narrow `body["messages"][index]` to a dict, for assertions on a mapped request body."""
    messages = body["messages"]
    assert isinstance(messages, list)
    message = messages[index]
    assert isinstance(message, dict)
    return message


# ──────────────────────────────────────────────────────────────────────────────
# request_to_json: full capabilities
# ──────────────────────────────────────────────────────────────────────────────


def test_request_to_json_puts_system_on_its_own_message_when_system_role_is_true() -> None:
    request = make_request(system="You are a bee.", messages=(Message.text(Role.USER, "Hi"),))

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert _wire_message(body, 0) == {"role": "system", "content": "You are a bee."}
    assert _wire_message(body, 1) == {"role": "user", "content": [{"type": "text", "text": "Hi"}]}
    assert body["model"] == "local-small"
    assert body["max_tokens"] == request.max_output_tokens


def test_request_to_json_sends_tools_and_tool_choice_when_native_tool_calls() -> None:
    request = make_request(tools=(make_tool(),))

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A tool used only by tests.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }
    ]
    assert body["tool_choice"] == "auto"


def test_request_to_json_omits_tool_choice_without_native_tool_calls() -> None:
    request = make_request(tools=(make_tool(),))
    capabilities = ProviderCapabilities.none()

    body = mapping.request_to_json(
        request, model="local-small", capabilities=capabilities, provider="p"
    )

    assert "tools" in body  # A prompted ladder still gets the tool definitions to render itself.
    assert "tool_choice" not in body


def test_request_to_json_uses_native_schema_when_schema_output() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    request = make_request(response_schema=schema)

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": schema, "strict": True},
    }


def test_request_to_json_falls_back_to_json_object_without_schema_output() -> None:
    request = make_request(response_schema={"type": "object"})
    capabilities = ProviderCapabilities.full().model_copy(update={"schema_output": False})

    body = mapping.request_to_json(
        request, model="local-small", capabilities=capabilities, provider="p"
    )

    assert body["response_format"] == {"type": "json_object"}


def test_request_to_json_omits_response_format_without_any_json_capability() -> None:
    request = make_request(response_schema={"type": "object"})
    capabilities = ProviderCapabilities.none()

    body = mapping.request_to_json(
        request, model="local-small", capabilities=capabilities, provider="p"
    )

    assert "response_format" not in body


def test_request_to_json_omits_response_format_without_a_schema() -> None:
    request = make_request(response_schema=None)

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert "response_format" not in body


def test_request_to_json_sends_reasoning_effort_only_with_reasoning_control() -> None:
    request = make_request(effort=Effort.HIGH)

    with_control = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )
    without_control = mapping.request_to_json(
        request,
        model="local-small",
        capabilities=ProviderCapabilities.none(),
        provider="p",
    )

    assert with_control["reasoning_effort"] == "high"
    assert "reasoning_effort" not in without_control


def test_request_to_json_sends_stop_sequences_and_temperature_when_set() -> None:
    request = make_request(stop_sequences=("STOP", "END"), temperature=0.2)

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert body["stop"] == ["STOP", "END"]
    assert body["temperature"] == 0.2


def test_request_to_json_omits_stop_and_temperature_when_unset() -> None:
    request = make_request()

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert "stop" not in body
    assert "temperature" not in body


# ──────────────────────────────────────────────────────────────────────────────
# request_to_json: content parts (images, tool calls, tool results)
# ──────────────────────────────────────────────────────────────────────────────


def test_request_to_json_maps_an_image_part_to_a_data_url_when_vision() -> None:
    message = Message(
        role=Role.USER, parts=(ImagePart(media_type="image/png", data_base64="Zm9v"),)
    )
    request = make_request(messages=(message,))

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    content = _wire_message(body, 0)["content"]
    assert content == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,Zm9v"}}]


def test_request_to_json_refuses_an_image_part_without_vision() -> None:
    message = Message(
        role=Role.USER, parts=(ImagePart(media_type="image/png", data_base64="Zm9v"),)
    )
    request = make_request(messages=(message,))
    capabilities = ProviderCapabilities.full().model_copy(update={"vision": False})

    with pytest.raises(ProviderRequestError, match="vision"):
        mapping.request_to_json(
            request, model="local-small", capabilities=capabilities, provider="p"
        )


def test_request_to_json_moves_a_tool_call_part_onto_tool_calls_not_content() -> None:
    call = ToolCall(id="c1", name="test_tool", arguments={"x": 1})
    message = Message(role=Role.ASSISTANT, parts=(ToolCallPart(call=call),))
    request = make_request(messages=(message,))

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    wire_message = _wire_message(body, 0)
    assert wire_message["role"] == "assistant"
    assert "content" not in wire_message
    assert wire_message["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "test_tool", "arguments": '{"x": 1}'}}
    ]


def test_request_to_json_splits_a_tool_result_into_its_own_tool_message() -> None:
    message = Message(
        role=Role.USER, parts=(ToolResultPart(call_id="c1", content="42", is_error=False),)
    )
    request = make_request(messages=(message,))

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert body["messages"] == [{"role": "tool", "tool_call_id": "c1", "content": "42"}]


def test_request_to_json_folds_is_error_into_the_tool_result_text() -> None:
    message = Message(
        role=Role.USER, parts=(ToolResultPart(call_id="c1", content="boom", is_error=True),)
    )
    request = make_request(messages=(message,))

    body = mapping.request_to_json(
        request, model="local-small", capabilities=ProviderCapabilities.full(), provider="p"
    )

    assert _wire_message(body, 0)["content"] == "ERROR: boom"


# ──────────────────────────────────────────────────────────────────────────────
# response_from_json
# ──────────────────────────────────────────────────────────────────────────────


def test_response_from_json_maps_a_text_completion() -> None:
    payload = _load_fixture("completion.json")

    response = mapping.response_from_json(payload, provider="p")

    assert response.text == "Hello there."
    assert response.stop_reason == StopReason.END_TURN
    assert response.model == "local-small"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 4
    assert response.usage.cached_tokens == 2
    assert response.usage.cost_usd is None


def test_response_from_json_maps_a_tool_call_completion() -> None:
    payload = _load_fixture("tool_call_completion.json")

    response = mapping.response_from_json(payload, provider="p")

    assert response.stop_reason == StopReason.TOOL_USE
    assert response.tool_calls == (
        ToolCall(id="call_fixture_1", name="get_weather", arguments={"city": "Paris"}),
    )


def test_response_from_json_raises_on_no_choices() -> None:
    with pytest.raises(MalformedOutputError):
        mapping.response_from_json({"choices": []}, provider="p")


def test_response_from_json_raises_on_malformed_tool_call_arguments() -> None:
    payload: JsonObject = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "t", "arguments": "{not json"}}
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    with pytest.raises(MalformedOutputError):
        mapping.response_from_json(payload, provider="p")


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("stop", StopReason.END_TURN),
        ("length", StopReason.MAX_TOKENS),
        ("tool_calls", StopReason.TOOL_USE),
        ("content_filter", StopReason.REFUSAL),
        ("something_new", StopReason.END_TURN),
        (None, StopReason.END_TURN),
    ],
)
def test_response_from_json_maps_every_finish_reason(
    finish_reason: str | None, expected: StopReason
) -> None:
    payload: JsonObject = {
        "choices": [{"message": {"content": "hi"}, "finish_reason": finish_reason}],
        "model": "local-small",
    }

    response = mapping.response_from_json(payload, provider="p")

    assert response.stop_reason == expected


# ──────────────────────────────────────────────────────────────────────────────
# StreamState
# ──────────────────────────────────────────────────────────────────────────────


def test_stream_state_absorbs_text_deltas_as_they_arrive() -> None:
    state = mapping.StreamState("p")

    chunks = state.absorb(
        {"choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]}
    )

    assert chunks == (LLMChunk(text="Hel"),)


def test_stream_state_emits_tool_call_only_once_finish_reason_arrives() -> None:
    state = mapping.StreamState("p")
    piece_1: JsonObject = {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "get_weather", "arguments": ""},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    piece_2: JsonObject = {
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"city":'}}]},
                "finish_reason": None,
            }
        ]
    }
    piece_3: JsonObject = {
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]},
                "finish_reason": "tool_calls",
            }
        ]
    }

    assert state.absorb(piece_1) == ()
    assert state.absorb(piece_2) == ()
    finished = state.absorb(piece_3)

    assert len(finished) == 1
    assert finished[0].tool_call == ToolCall(
        id="call_1", name="get_weather", arguments={"city": "Paris"}
    )


def test_stream_state_flushes_stop_reason_on_the_trailing_usage_chunk() -> None:
    state = mapping.StreamState("p")
    state.absorb({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})

    final = state.absorb(
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}
    )

    assert len(final) == 1
    assert final[0].stop_reason == StopReason.END_TURN
    assert final[0].usage is not None
    assert final[0].usage.input_tokens == 5
    assert state.finalize() is None  # Already flushed; nothing left to say.


def test_stream_state_finalize_covers_a_server_with_no_trailing_usage_chunk() -> None:
    state = mapping.StreamState("p")
    state.absorb({"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]})

    trailing = state.finalize()

    assert trailing is not None
    assert trailing.stop_reason == StopReason.MAX_TOKENS
    assert trailing.usage is None


def test_stream_state_raises_malformed_output_for_unparseable_tool_call_arguments() -> None:
    state = mapping.StreamState("p")
    state.absorb(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "t", "arguments": "{bad"}}
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
    )

    with pytest.raises(MalformedOutputError):
        state.absorb({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
