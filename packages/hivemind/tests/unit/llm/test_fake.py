"""Tests for hivemind.llm.fake: FakeLLMProvider, text_response and tool_call_response.

Fits into the Hive:
    Mirrors src/hivemind/llm/fake.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fake for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_request, text_response, tool_call_response

from hivemind.llm.capabilities import HealthState, ProviderCapabilities
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.fake import CHARS_PER_TOKEN_ESTIMATE, FAKE_MODEL_ID, FakeLLMProvider
from hivemind.llm.models import LLMRequest, LLMResponse, Message, Role, StopReason, ToolCall
from waggle.clock import FakeClock

# ──────────────────────────────────────────────────────────────────────────────
# text_response / tool_call_response
# ──────────────────────────────────────────────────────────────────────────────


def test_text_response_builds_a_single_text_part_response_with_zero_usage() -> None:
    response = text_response("hello")

    assert response.text == "hello"
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.input_tokens == 0
    assert response.model == FAKE_MODEL_ID


def test_tool_call_response_builds_one_part_per_call_with_tool_use_stop() -> None:
    calls = (ToolCall(id="c1", name="t", arguments={}), ToolCall(id="c2", name="t", arguments={}))

    response = tool_call_response(*calls)

    assert response.tool_calls == calls
    assert response.stop_reason is StopReason.TOOL_USE


# ──────────────────────────────────────────────────────────────────────────────
# Construction and scripting
# ──────────────────────────────────────────────────────────────────────────────


def test_fake_provider_defaults_to_full_capabilities_and_no_calls() -> None:
    provider = FakeLLMProvider()

    assert provider.name == "fake"
    assert provider.capabilities == ProviderCapabilities.full()
    assert provider.calls == []


async def test_script_returns_responses_in_fifo_order_and_records_every_call() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("first"), text_response("second"))
    first_request = make_request()
    second_request = make_request(max_output_tokens=2048)

    first = await provider.complete(first_request)
    second = await provider.complete(second_request)

    assert (first.text, second.text) == ("first", "second")
    assert provider.calls == [first_request, second_request]


async def test_scripted_llm_error_is_raised_not_returned() -> None:
    provider = FakeLLMProvider()
    provider.script(ProviderUnavailableError("fake", "scripted failure"))

    with pytest.raises(ProviderUnavailableError, match="scripted failure"):
        await provider.complete(make_request())


async def test_an_empty_script_raises_provider_unavailable_not_index_error() -> None:
    provider = FakeLLMProvider()

    with pytest.raises(ProviderUnavailableError, match="ran dry"):
        await provider.complete(make_request())


async def test_a_custom_responder_bypasses_the_scripted_queue() -> None:
    def echo_slot(request: LLMRequest) -> LLMResponse:
        return text_response(request.slot.value)

    provider = FakeLLMProvider(responder=echo_slot)

    response = await provider.complete(make_request())

    assert response.text == "WORKER"
    assert provider.calls  # still recorded, even though script() was never called.


# ──────────────────────────────────────────────────────────────────────────────
# Outage
# ──────────────────────────────────────────────────────────────────────────────


async def test_outage_makes_complete_raise_before_touching_calls_or_script() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("unused"))
    provider.set_outage(True)

    with pytest.raises(ProviderUnavailableError, match="outage"):
        await provider.complete(make_request())

    assert provider.calls == []  # The call never happened, by this fake's own honest accounting.


async def test_outage_makes_stream_raise_on_first_iteration() -> None:
    provider = FakeLLMProvider()
    provider.set_outage(True)

    with pytest.raises(ProviderUnavailableError, match="outage"):
        async for _ in provider.stream(make_request()):
            pass


async def test_clearing_the_outage_lets_calls_through_again() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("back"))
    provider.set_outage(True)
    provider.set_outage(False)

    response = await provider.complete(make_request())

    assert response.text == "back"


async def test_health_reflects_outage_state() -> None:
    provider = FakeLLMProvider(clock=FakeClock())

    healthy = await provider.health()
    provider.set_outage(True)
    down = await provider.health()

    assert healthy.state is HealthState.HEALTHY
    assert down.state is HealthState.DOWN


# ──────────────────────────────────────────────────────────────────────────────
# Capability honesty
# ──────────────────────────────────────────────────────────────────────────────


async def test_none_capabilities_drops_scripted_tool_call_parts() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    call = ToolCall(id="c1", name="t", arguments={})
    provider.script(tool_call_response(call))

    response = await provider.complete(make_request())

    assert response.tool_calls == ()


async def test_full_capabilities_keeps_scripted_tool_call_parts() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    call = ToolCall(id="c1", name="t", arguments={})
    provider.script(tool_call_response(call))

    response = await provider.complete(make_request())

    assert response.tool_calls == (call,)


async def test_none_capabilities_ignores_an_unsupported_response_schema() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response("plain text anyway"))
    request = make_request(response_schema={"type": "object"})

    response = await provider.complete(request)  # Does not raise despite schema_output=False.

    assert response.text == "plain text anyway"


# ──────────────────────────────────────────────────────────────────────────────
# Streaming
# ──────────────────────────────────────────────────────────────────────────────


async def test_streaming_provider_yields_text_chunks_that_reassemble_the_response() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("abcdefghi"))

    chunks = [chunk async for chunk in provider.stream(make_request())]

    reassembled = "".join(chunk.text for chunk in chunks if chunk.text is not None)
    assert reassembled == "abcdefghi"
    assert chunks[-1].stop_reason is StopReason.END_TURN
    assert chunks[-1].usage is not None


async def test_streaming_provider_yields_tool_call_chunks() -> None:
    provider = FakeLLMProvider()
    call = ToolCall(id="c1", name="t", arguments={})
    provider.script(tool_call_response(call))

    chunks = [chunk async for chunk in provider.stream(make_request())]

    tool_call_chunks = [chunk.tool_call for chunk in chunks if chunk.tool_call is not None]
    assert tool_call_chunks == [call]


async def test_non_streaming_capabilities_yield_exactly_one_chunk() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response("whole thing at once"))

    chunks = [chunk async for chunk in provider.stream(make_request())]

    assert len(chunks) == 1
    assert chunks[0].text == "whole thing at once"
    assert chunks[0].stop_reason is StopReason.END_TURN


# ──────────────────────────────────────────────────────────────────────────────
# count_tokens
# ──────────────────────────────────────────────────────────────────────────────


async def test_count_tokens_estimates_from_system_and_text_parts() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    request = make_request(system="1234", messages=(Message.text(Role.USER, "12345678"),))

    estimate = await provider.count_tokens(request)

    assert estimate == (4 + 8) // CHARS_PER_TOKEN_ESTIMATE


async def test_count_tokens_is_none_without_the_capability() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())

    estimate = await provider.count_tokens(make_request())

    assert estimate is None
