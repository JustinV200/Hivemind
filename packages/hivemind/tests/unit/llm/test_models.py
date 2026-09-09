"""Tests for hivemind.llm.models: the request/message/response boundary shapes.

Fits into the Hive:
    Mirrors src/hivemind/llm/models.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.models for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_request, make_response, make_tool
from pydantic import ValidationError

from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.models import (
    ContentPart,
    ImagePart,
    LLMChunk,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
    Usage,
)

# ──────────────────────────────────────────────────────────────────────────────
# ToolDefinition / ToolCall
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_definition_round_trips_through_json() -> None:
    tool = make_tool(parameters={"type": "object", "properties": {"x": {"type": "integer"}}})

    restored = tool.__class__.model_validate_json(tool.model_dump_json())

    assert restored == tool


@pytest.mark.parametrize("name", ["Bad-Name", "1tool", "", "has space"])
def test_tool_definition_rejects_a_malformed_name(name: str) -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        make_tool(name=name)


def test_tool_call_round_trips_through_json() -> None:
    call = ToolCall(id="call_1", name="test_tool", arguments={"x": 1, "nested": {"y": [1, 2]}})

    restored = ToolCall.model_validate_json(call.model_dump_json())

    assert restored == call


def test_tool_call_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        ToolCall.model_validate({"id": "c", "name": "t", "arguments": {}, "bogus": 1})


# ──────────────────────────────────────────────────────────────────────────────
# Content parts and the ContentPart discriminated union
# ──────────────────────────────────────────────────────────────────────────────


def test_text_part_round_trips_through_json() -> None:
    part = TextPart(text="hello")

    restored = TextPart.model_validate_json(part.model_dump_json())

    assert restored == part


def test_image_part_round_trips_through_json() -> None:
    part = ImagePart(media_type="image/png", data_base64="AAAA")

    restored = ImagePart.model_validate_json(part.model_dump_json())

    assert restored == part


def test_tool_result_part_defaults_is_error_false() -> None:
    part = ToolResultPart(call_id="call_1", content="42")

    assert part.is_error is False


def test_content_part_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Message(role=Role.USER, parts=({"kind": "audio", "data": "x"},))


@pytest.mark.parametrize(
    ("part", "expected_type"),
    [
        (TextPart(text="hi"), TextPart),
        (ImagePart(media_type="image/png", data_base64="AA"), ImagePart),
        (ToolCallPart(call=ToolCall(id="c", name="t", arguments={})), ToolCallPart),
        (ToolResultPart(call_id="c", content="ok"), ToolResultPart),
    ],
)
def test_message_round_trip_preserves_each_content_part_subtype(
    part: ContentPart, expected_type: type
) -> None:
    message = Message(role=Role.USER, parts=(part,))

    restored = Message.model_validate_json(message.model_dump_json())

    assert restored == message
    assert isinstance(restored.parts[0], expected_type)


# ──────────────────────────────────────────────────────────────────────────────
# Message
# ──────────────────────────────────────────────────────────────────────────────


def test_message_text_helper_builds_a_single_text_part_message() -> None:
    message = Message.text(Role.ASSISTANT, "hello there")

    assert message.role is Role.ASSISTANT
    assert message.parts == (TextPart(text="hello there"),)


def test_message_is_frozen() -> None:
    message = Message.text(Role.USER, "hi")

    with pytest.raises(ValidationError, match="frozen"):
        message.role = Role.ASSISTANT  # type: ignore[misc]  # The assignment is the test.


# ──────────────────────────────────────────────────────────────────────────────
# Usage
# ──────────────────────────────────────────────────────────────────────────────


def test_usage_defaults_cached_tokens_and_cost_usd() -> None:
    usage = Usage(input_tokens=10, output_tokens=5)

    assert usage.cached_tokens == 0
    assert usage.cost_usd is None


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cached_tokens"])
def test_usage_rejects_a_negative_token_count(field: str) -> None:
    fields: dict[str, object] = {"input_tokens": 1, "output_tokens": 1, field: -1}

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Usage(**fields)


def test_usage_add_sums_token_counts_and_cost_when_both_known() -> None:
    first = Usage(input_tokens=10, output_tokens=5, cached_tokens=2, cost_usd=0.01)
    second = Usage(input_tokens=3, output_tokens=1, cached_tokens=0, cost_usd=0.02)

    total = first + second

    assert total == Usage(input_tokens=13, output_tokens=6, cached_tokens=2, cost_usd=0.03)


def test_usage_add_is_none_aware_on_cost() -> None:
    priced = Usage(input_tokens=10, output_tokens=5, cost_usd=0.01)
    unpriced = Usage(input_tokens=3, output_tokens=1)

    total = priced + unpriced

    assert total.cost_usd is None
    assert total.input_tokens == 13


def test_usage_round_trips_through_json() -> None:
    usage = Usage(input_tokens=10, output_tokens=5, cached_tokens=1, cost_usd=0.5)

    restored = Usage.model_validate_json(usage.model_dump_json())

    assert restored == usage


def test_usage_add_rejects_a_non_usage_operand() -> None:
    usage = Usage(input_tokens=1, output_tokens=1)

    with pytest.raises(TypeError):
        usage + 1  # type: ignore[operator]  # The unsupported operand is the test.


# ──────────────────────────────────────────────────────────────────────────────
# LLMRequest
# ──────────────────────────────────────────────────────────────────────────────


def test_llm_request_defaults_tools_effort_and_stop_sequences() -> None:
    request = make_request()

    assert request.tools == ()
    assert request.effort is Effort.MEDIUM
    assert request.stop_sequences == ()
    assert request.temperature is None
    assert request.response_schema is None
    assert request.system is None


def test_llm_request_accepts_a_response_schema_as_a_json_object() -> None:
    schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "str"}}}

    request = make_request(response_schema=schema)

    assert request.response_schema == schema


@pytest.mark.parametrize("max_output_tokens", [0, -1])
def test_llm_request_rejects_a_non_positive_max_output_tokens(max_output_tokens: int) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        make_request(max_output_tokens=max_output_tokens)


def test_llm_request_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        make_request(bogus="nope")


def test_llm_request_round_trips_through_json() -> None:
    request = make_request(
        slot=ModelSlot.QUEEN,
        system="Be helpful.",
        tools=(make_tool(),),
        stop_sequences=("STOP",),
        temperature=0.2,
    )

    restored = LLMRequest.model_validate_json(request.model_dump_json())

    assert restored == request


# ──────────────────────────────────────────────────────────────────────────────
# LLMResponse
# ──────────────────────────────────────────────────────────────────────────────


def test_llm_response_text_property_joins_only_text_parts() -> None:
    call = ToolCall(id="c", name="t", arguments={})
    response = make_response(
        parts=(TextPart(text="a"), ToolCallPart(call=call), TextPart(text="b"))
    )

    assert response.text == "ab"
    assert response.tool_calls == (call,)


def test_llm_response_tool_calls_is_empty_with_no_tool_call_parts() -> None:
    response = make_response()

    assert response.tool_calls == ()


def test_llm_response_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        make_response(bogus="nope")


def test_llm_response_round_trips_through_json() -> None:
    response = make_response(reasoning_summary="thought about it briefly")

    restored = LLMResponse.model_validate_json(response.model_dump_json())

    assert restored == response


# ──────────────────────────────────────────────────────────────────────────────
# LLMChunk
# ──────────────────────────────────────────────────────────────────────────────


def test_llm_chunk_defaults_every_field_to_none() -> None:
    chunk = LLMChunk()

    assert chunk.text is None
    assert chunk.tool_call is None
    assert chunk.usage is None
    assert chunk.stop_reason is None


def test_llm_chunk_round_trips_through_json() -> None:
    chunk = LLMChunk(
        text="partial",
        usage=Usage(input_tokens=1, output_tokens=1),
        stop_reason=StopReason.END_TURN,
    )

    restored = LLMChunk.model_validate_json(chunk.model_dump_json())

    assert restored == chunk


def test_llm_chunk_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        LLMChunk.model_validate({"text": "hi", "bogus": True})


def test_llm_request_model_defaults_to_none_and_round_trips_when_set() -> None:
    unstamped = make_request()
    stamped = make_request(model="local-small")

    assert unstamped.model is None
    assert LLMRequest.model_validate_json(stamped.model_dump_json()) == stamped
