"""Tests for hivemind.llm.providers.openai_compat.estimate: usage guessed from characters.

Covers the estimator itself and the two places `mapping.py` falls back to it: a non-streaming
reply and a stream whose server never sent a usage block.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/estimate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.estimate for the module under test.
"""

from __future__ import annotations

from builders.llm import make_request, make_tool

from hivemind.llm.models import JsonObject, StopReason, TextPart, ToolCall, ToolCallPart
from hivemind.llm.providers.openai_compat import mapping
from hivemind.llm.providers.openai_compat.estimate import (
    CHARS_PER_TOKEN_ESTIMATE,
    estimated_usage,
    request_char_count,
    response_char_count,
    tokens_from_chars,
)


def _reply(content: str, usage: JsonObject | None) -> JsonObject:
    """Build a minimal chat-completions reply, with or without a usage block."""
    payload: JsonObject = {
        "model": "local-small",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def test_tokens_from_chars_rounds_up_so_any_text_costs_a_token() -> None:
    assert tokens_from_chars(0) == 0
    assert tokens_from_chars(1) == 1
    assert tokens_from_chars(CHARS_PER_TOKEN_ESTIMATE * 3) == 3
    assert tokens_from_chars(CHARS_PER_TOKEN_ESTIMATE * 3 + 1) == 4


def test_estimated_usage_is_flagged_and_unpriced() -> None:
    usage = estimated_usage(prompt_chars=40, output_chars=8)

    assert (usage.input_tokens, usage.output_tokens) == (10, 2)
    assert usage.estimated is True
    assert usage.cost_usd is None


def test_request_char_count_sums_system_text_and_tools() -> None:
    tool = make_tool()
    request = make_request(system="abcd", tools=(tool,))

    expected = 4 + len("Say hello.") + len(tool.name) + len(tool.description)
    assert request_char_count(request) >= expected


def test_response_char_count_counts_text_and_tool_call_names_and_arguments() -> None:
    call = ToolCall(id="c1", name="lookup", arguments={"q": "x"})

    chars = response_char_count((TextPart(text="hello"), ToolCallPart(call=call)))

    assert chars == len("hello") + len("lookup") + len(str({"q": "x"}))


def test_response_from_json_estimates_usage_when_the_server_sent_none() -> None:
    response = mapping.response_from_json(
        _reply("a" * 40, usage=None), provider="p", prompt_chars=80
    )

    assert response.usage.estimated is True
    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 10


def test_response_from_json_treats_a_usage_without_token_counts_as_missing() -> None:
    response = mapping.response_from_json(_reply("abcd", usage={}), provider="p", prompt_chars=4)

    assert response.usage.estimated is True


def test_response_from_json_keeps_the_servers_own_counts_unflagged() -> None:
    usage: JsonObject = {"prompt_tokens": 7, "completion_tokens": 3}

    response = mapping.response_from_json(_reply("abcd", usage=usage), provider="p")

    assert (response.usage.input_tokens, response.usage.output_tokens) == (7, 3)
    assert response.usage.estimated is False


def test_response_from_json_keeps_an_honest_zero_count_unflagged() -> None:
    usage: JsonObject = {"prompt_tokens": 0, "completion_tokens": 0}

    response = mapping.response_from_json(_reply("", usage=usage), provider="p", prompt_chars=40)

    assert response.usage.estimated is False
    assert response.usage.input_tokens == 0


def test_stream_state_estimates_from_streamed_characters_when_no_usage_arrives() -> None:
    state = mapping.StreamState("p", prompt_chars=16)
    state.absorb({"choices": [{"index": 0, "delta": {"content": "abcdefgh"}}]})
    state.absorb({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})

    trailing = state.finalize()

    assert trailing is not None
    assert trailing.stop_reason == StopReason.END_TURN
    assert trailing.usage is not None
    assert (trailing.usage.input_tokens, trailing.usage.output_tokens) == (4, 2)
    assert trailing.usage.estimated is True


def test_stream_state_does_not_estimate_when_the_server_sent_usage() -> None:
    state = mapping.StreamState("p", prompt_chars=16)
    state.absorb({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    state.absorb({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    assert state.finalize() is None
