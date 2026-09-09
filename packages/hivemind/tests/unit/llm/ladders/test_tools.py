"""Tests for hivemind.llm.ladders.tools: run_tool_loop, its native and prompted protocols.

Fits into the Hive:
    Mirrors src/hivemind/llm/ladders/tools.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.ladders.tools for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import (
    make_bound,
    make_request,
    make_tool,
    make_tool_call,
    text_response,
    tool_call_response,
)

from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError, RefusedError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.ladders.observer import FallbackReason
from hivemind.llm.ladders.tools import ToolLoopOptions, run_tool_loop
from hivemind.llm.models import StopReason, ToolCall, ToolResultPart, Usage


class _RecordingExecutor:
    """A ToolExecutor that records every call it received and echoes back its arguments."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResultPart:
        self.calls.append(call)
        return ToolResultPart(call_id=call.id, content=f"ok:{call.name}")


class _RecordingObserver:
    """A LadderObserver that records every note it sees, for assertions."""

    def __init__(self) -> None:
        self.notes: list[object] = []

    async def on_fallback(self, note: object) -> None:
        self.notes.append(note)


_TOOL = make_tool(
    name="lookup",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)


# ──────────────────────────────────────────────────────────────────────────────
# Native protocol
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_tool_loop_native_protocol_executes_a_call_then_returns_the_final_text() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    call = make_tool_call(name="lookup", arguments={"query": "bees"})
    provider.script(tool_call_response(call), text_response("Bees make honey."))
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert result.final_text == "Bees make honey."
    assert result.stop_reason is StopReason.END_TURN
    assert result.is_exhausted is False
    assert result.calls == (call,)
    assert len(executor.calls) == 1


async def test_run_tool_loop_native_protocol_sends_the_offered_tools_every_round() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(text_response("no tools needed"))

    await run_tool_loop(
        make_bound(provider=provider), make_request(), (_TOOL,), _RecordingExecutor()
    )

    assert provider.calls[0].tools == (_TOOL,)


async def test_run_tool_loop_native_protocol_executes_calls_concurrently_when_parallel() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    first_call = make_tool_call(id="c1", name="lookup", arguments={"query": "a"})
    second_call = make_tool_call(id="c2", name="lookup", arguments={"query": "b"})
    provider.script(tool_call_response(first_call, second_call), text_response("done"))
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert result.calls == (first_call, second_call)
    assert len(executor.calls) == 2


async def test_run_tool_loop_sums_usage_across_rounds() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    call = make_tool_call(name="lookup", arguments={"query": "bees"})
    provider.script(
        tool_call_response(call).model_copy(
            update={"usage": Usage(input_tokens=10, output_tokens=1)}
        ),
        text_response("done", usage=Usage(input_tokens=20, output_tokens=2)),
    )

    result = await run_tool_loop(
        make_bound(provider=provider), make_request(), (_TOOL,), _RecordingExecutor()
    )

    assert result.usage == Usage(input_tokens=30, output_tokens=3)


async def test_run_tool_loop_raises_refused_error_on_a_refusal_stop_reason() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(text_response("no.", stop=StopReason.REFUSAL))

    with pytest.raises(RefusedError):
        await run_tool_loop(
            make_bound(provider=provider), make_request(), (_TOOL,), _RecordingExecutor()
        )


# ──────────────────────────────────────────────────────────────────────────────
# Invalid arguments: rejected before execution, on either protocol
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_tool_loop_native_protocol_rejects_invalid_arguments_without_executing() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    bad_call = make_tool_call(name="lookup", arguments={})  # Missing the required "query".
    provider.script(tool_call_response(bad_call), text_response("done"))
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert executor.calls == []
    assert result.calls == (bad_call,)


async def test_run_tool_loop_native_protocol_rejects_a_call_to_an_unoffered_tool() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    call = make_tool_call(name="not_offered", arguments={})
    provider.script(tool_call_response(call), text_response("done"))
    executor = _RecordingExecutor()

    await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert executor.calls == []


# ──────────────────────────────────────────────────────────────────────────────
# Prompted protocol
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_tool_loop_prompted_protocol_executes_a_fenced_call_then_returns_final_text() -> (
    None
):
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(
        text_response('```tool\n{"name": "lookup", "arguments": {"query": "bees"}}\n```'),
        text_response("Bees make honey."),
    )
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert result.final_text == "Bees make honey."
    assert result.calls[0].name == "lookup"
    assert result.calls[0].id == "call_1"
    assert len(executor.calls) == 1


async def test_run_tool_loop_prompted_protocol_offers_no_native_tools() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response("no calls, plain answer"))

    await run_tool_loop(
        make_bound(provider=provider), make_request(), (_TOOL,), _RecordingExecutor()
    )

    assert provider.calls[0].tools == ()


async def test_run_tool_loop_prompted_protocol_rejects_invalid_arguments() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(
        text_response('```tool\n{"name": "lookup", "arguments": {}}\n```'), text_response("done")
    )
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert executor.calls == []
    assert result.calls[0].name == "lookup"


async def test_run_tool_loop_prompted_protocol_handles_a_malformed_block_as_an_error() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response("```tool\nnot json at all\n```"), text_response("done"))
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert executor.calls == []
    assert result.calls[0].name == ""


async def test_run_tool_loop_prompted_protocol_handles_a_block_that_is_not_an_object() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response("```tool\n[1, 2, 3]\n```"), text_response("done"))
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert executor.calls == []
    assert result.calls[0].name == ""


async def test_run_tool_loop_prompted_protocol_handles_a_block_missing_name_or_arguments() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response('```tool\n{"name": "lookup"}\n```'), text_response("done"))
    executor = _RecordingExecutor()

    result = await run_tool_loop(make_bound(provider=provider), make_request(), (_TOOL,), executor)

    assert executor.calls == []
    assert result.calls[0].name == ""


async def test_run_tool_loop_prompted_protocol_raises_refused_error_on_refusal() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response("no.", stop=StopReason.REFUSAL))

    with pytest.raises(RefusedError):
        await run_tool_loop(
            make_bound(provider=provider), make_request(), (_TOOL,), _RecordingExecutor()
        )


async def test_run_tool_loop_prompted_protocol_reports_exhausted_when_max_rounds_is_hit() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    block = '```tool\n{"name": "lookup", "arguments": {"query": "bees"}}\n```'
    provider.script(text_response(block), text_response(block))

    result = await run_tool_loop(
        make_bound(provider=provider),
        make_request(),
        (_TOOL,),
        _RecordingExecutor(),
        ToolLoopOptions(max_rounds=2),
    )

    assert result.is_exhausted is True
    assert result.rounds == 2


# ──────────────────────────────────────────────────────────────────────────────
# max_rounds
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_tool_loop_reports_exhausted_when_max_rounds_is_hit() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    call = make_tool_call(name="lookup", arguments={"query": "bees"})
    provider.script(tool_call_response(call), tool_call_response(call))

    result = await run_tool_loop(
        make_bound(provider=provider),
        make_request(),
        (_TOOL,),
        _RecordingExecutor(),
        ToolLoopOptions(max_rounds=2),
    )

    assert result.is_exhausted is True
    assert result.rounds == 2


async def test_run_tool_loop_rejects_a_max_rounds_below_one() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())

    with pytest.raises(ValueError, match="max_rounds"):
        await run_tool_loop(
            make_bound(provider=provider),
            make_request(),
            (_TOOL,),
            _RecordingExecutor(),
            ToolLoopOptions(max_rounds=0),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Fallback chain
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_tool_loop_falls_back_to_the_next_binding_on_provider_unavailable() -> None:
    primary = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    primary.script(ProviderUnavailableError("primary", "down"))
    fallback_provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    fallback_provider.script(text_response("answered on the fallback"))
    fallback_bound = make_bound(provider=fallback_provider, binding="local_worker")
    bound = make_bound(provider=primary, binding="worker", fallback=fallback_bound)
    observer = _RecordingObserver()

    result = await run_tool_loop(
        bound, make_request(), (_TOOL,), _RecordingExecutor(), ToolLoopOptions(observer=observer)
    )

    assert result.final_text == "answered on the fallback"
    assert observer.notes[0].reason is FallbackReason.PROVIDER_UNAVAILABLE  # type: ignore[attr-defined]


async def test_run_tool_loop_falls_back_to_the_next_binding_on_rate_limited() -> None:
    primary = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    primary.script(RateLimitedError("primary"))
    fallback_provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    fallback_provider.script(text_response("answered on the fallback"))
    fallback_bound = make_bound(provider=fallback_provider, binding="local_worker")
    bound = make_bound(provider=primary, binding="worker", fallback=fallback_bound)
    observer = _RecordingObserver()

    result = await run_tool_loop(
        bound, make_request(), (_TOOL,), _RecordingExecutor(), ToolLoopOptions(observer=observer)
    )

    assert result.final_text == "answered on the fallback"
    assert observer.notes[0].reason is FallbackReason.RATE_LIMITED  # type: ignore[attr-defined]


async def test_run_tool_loop_reraises_when_no_fallback_binding_exists() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(ProviderUnavailableError("primary", "down"))

    with pytest.raises(ProviderUnavailableError):
        await run_tool_loop(
            make_bound(provider=provider), make_request(), (_TOOL,), _RecordingExecutor()
        )
