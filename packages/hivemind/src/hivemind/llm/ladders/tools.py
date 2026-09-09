"""Implement run_tool_loop: the tool-call degradation ladder (roadmap step 3.5).

Codingrules section 8.6 ("degrade by ladder, in one place"): a Worker's tool loop (the Drone,
`workers/roles/drone.py`, a later roadmap step, is the first caller) never asks whether its bound
provider has a native tool-call protocol. It calls `run_tool_loop`, and this module runs the
native protocol (send `tools`, execute what comes back, loop) when the binding declares
`native_tool_calls`, or the prompted protocol (a preamble listing every tool, fenced ```tool```
blocks parsed back out) otherwise -- so the same call site works unchanged on the strongest hosted
model and the weakest local one. Every call, on either protocol, is validated against its tool's
JSON-schema `parameters` (`hivemind.llm.ladders.extraction.validate_arguments`) before it is
executed, because a model's tool call is untrusted input (codingrules section 15) exactly like any
other model output.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.ladders`. Called by a
    Worker's tool loop (roadmap step 3.16, a later dispatch). Calls into `hivemind.llm.ladders`'
    siblings (`extraction`, `gate`, `observer`), `hivemind.llm.errors`, `hivemind.llm.models` and
    `hivemind.llm.slots` only.

Key invariants:
    - Every call, native or prompted, is validated with `validate_arguments` before it reaches
      `ToolExecutor.execute`; an invalid call is never executed and its errors travel back to the
      model as an `is_error` `ToolResultPart`, never as a raised exception.
    - Native calls run concurrently only when the binding declares `parallel_tool_calls`;
      otherwise, and always under the prompted protocol, calls run one at a time, in order.
    - `is_exhausted` is true exactly when `options.max_rounds` was reached while the model still
      wanted to call a tool; `final_text` is always the last response's text, even then.
    - This is the one ladder module `scripts/check_no_transcripts.py` allowlists for a `messages`/
      `history`-named local (per its own docstring): a tool loop legitimately accumulates turns
      within one call, unlike `hivemind.llm.ladders.structured`, which never does.

See Also:
    - .claude/codingrules.md section 8.6 for "degrade by ladder, in one place".
    - .claude/codingrules.md section 15 for "LLM output is untrusted input".
    - docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md for the decision this
      module implements.
    - hivemind.llm.ladders.extraction for validate_arguments and the prompted tool preamble.
    - hivemind.llm.ladders.structured for the sibling ladder this one mirrors in shape.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from hivemind.forage.slots import ModelSlot
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError, RefusedError
from hivemind.llm.ladders.extraction import (
    extract_tool_blocks,
    render_tool_preamble,
    validate_arguments,
)
from hivemind.llm.ladders.gate import CallGate, DirectCallGate
from hivemind.llm.ladders.observer import (
    FallbackNote,
    FallbackReason,
    LadderObserver,
    NullLadderObserver,
)
from hivemind.llm.models import (
    JsonObject,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StopReason,
    ToolCall,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
)
from hivemind.llm.slots import BoundModel

MAX_TOOL_ROUNDS_DEFAULT = 12  # Generous for a real task, small enough to bound a runaway loop.

__all__ = [
    "MAX_TOOL_ROUNDS_DEFAULT",
    "ToolExecutor",
    "ToolLoopOptions",
    "ToolLoopResult",
    "run_tool_loop",
]


class ToolExecutor(Protocol):
    """Execute one validated ToolCall and return its result.

    Implemented per Worker role, over whatever tool registry that role's capabilities allow
    (roadmap step 3.16, a later dispatch).
    """

    async def execute(self, call: ToolCall) -> ToolResultPart:
        """Run `call` and return its result.

        Args:
            call: The tool call to run; already validated against its tool's schema.

        Returns:
            A ToolResultPart naming `call.id`; `is_error=True` when the tool itself failed.
        """
        ...


@dataclass(frozen=True, slots=True)
class ToolLoopOptions:
    """Group `run_tool_loop`'s optional collaborators, so the function stays within 5 parameters."""

    max_rounds: int = MAX_TOOL_ROUNDS_DEFAULT  # Cap on model turns before giving up as exhausted.
    gate: CallGate | None = None  # DirectCallGate() (no metering) when None.
    observer: LadderObserver | None = None  # NullLadderObserver() (discards) when None.


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    """One `run_tool_loop` call's outcome."""

    final_text: str  # The last response's text (may be empty if it only called tools).
    rounds: int  # How many model turns the winning binding made.
    calls: tuple[ToolCall, ...]  # Every call made, across every round, in order.
    usage: Usage  # Summed token/cost accounting across every round.
    stop_reason: StopReason  # The last response's stop reason.
    is_exhausted: bool  # True when max_rounds was hit while the model still wanted to call a tool.


@dataclass(frozen=True, slots=True)
class _LoopState:
    """Group one binding attempt's fixed collaborators, so protocol helpers stay under 5 params."""

    current: BoundModel
    tools: tuple[ToolDefinition, ...]
    executor: ToolExecutor
    gate: CallGate


async def run_tool_loop(
    bound: BoundModel,
    request: LLMRequest,
    tools: tuple[ToolDefinition, ...],
    executor: ToolExecutor,
    options: ToolLoopOptions | None = None,
) -> ToolLoopResult:
    """Run a tool-calling conversation on `bound`, degrading protocol and bindings as needed.

    Args:
        bound: The binding to call, and its fallback chain.
        request: The call to make; its `tools` field is overwritten each round, never read.
        tools: The tools the model may call this loop.
        executor: Runs a validated call and returns its result.
        options: Round cap, gate and observer; defaults to `ToolLoopOptions()` when omitted.

    Returns:
        A ToolLoopResult with the final text, every call made, summed usage and whether the round
        cap was hit before the model stopped wanting to call tools.

    Raises:
        RefusedError: A response's `stop_reason` was REFUSAL.
        ContextTooLongError: Propagated unchanged; the caller must shrink `request` and retry.
        ValueError: `options.max_rounds` is less than 1.
    """
    active_options = options if options is not None else ToolLoopOptions()
    if active_options.max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {active_options.max_rounds}.")
    gate = active_options.gate if active_options.gate is not None else DirectCallGate()
    observer = (
        active_options.observer if active_options.observer is not None else NullLadderObserver()
    )
    current = bound
    while True:
        state = _LoopState(current=current, tools=tools, executor=executor, gate=gate)
        try:
            return await _run_tool_binding(state, request, active_options.max_rounds)
        except (ProviderUnavailableError, RateLimitedError) as exc:
            fallback = current.fallback
            if fallback is None:
                raise
            await observer.on_fallback(_outage_note(bound.slot, current, fallback, exc))
            current = fallback


def _outage_note(
    slot: ModelSlot,
    current: BoundModel,
    fallback: BoundModel,
    exc: ProviderUnavailableError | RateLimitedError,
) -> FallbackNote:
    """Build the FallbackNote for moving from `current` to `fallback` after `exc`."""
    reason = (
        FallbackReason.RATE_LIMITED
        if isinstance(exc, RateLimitedError)
        else FallbackReason.PROVIDER_UNAVAILABLE
    )
    return FallbackNote(
        slot=slot,
        from_binding=current.binding,
        to_binding=fallback.binding,
        from_rung=None,
        to_rung=None,
        reason=reason,
    )


async def _run_tool_binding(
    state: _LoopState, request: LLMRequest, max_rounds: int
) -> ToolLoopResult:
    """Run `state.current`'s own protocol (native or prompted) to completion or exhaustion."""
    if state.current.provider.capabilities.native_tool_calls:
        return await _native_protocol(state, request, max_rounds)
    return await _prompted_protocol(state, request, max_rounds)


async def _native_protocol(
    state: _LoopState, request: LLMRequest, max_rounds: int
) -> ToolLoopResult:
    """Run the native tool-call protocol: send `tools`, execute TOOL_USE turns, loop.

    Written as an unconditional loop whose every exit is inside the body (never a post-loop read
    of the last response): `run_tool_loop`'s own `max_rounds >= 1` guard makes at least one round
    always run, but reading a round variable after the loop would still need an `Optional` type
    and an `assert` to narrow it back, which `ruff`'s `S101` (no bare `assert` outside tests)
    forbids anyway.
    """
    conversation = request.messages
    usage = Usage(input_tokens=0, output_tokens=0)
    calls: list[ToolCall] = []
    parallel = state.current.provider.capabilities.parallel_tool_calls
    round_number = 0
    while True:
        round_number += 1
        attempt = request.model_copy(update={"messages": conversation, "tools": state.tools})
        response = await state.gate.complete(state.current, attempt)
        usage = usage + response.usage
        if response.stop_reason is StopReason.REFUSAL:
            raise RefusedError(
                state.current.provider.name, response.reasoning_summary or "no reason given"
            )
        if response.stop_reason is not StopReason.TOOL_USE:
            return _loop_result(response, round_number, calls, usage, is_exhausted=False)
        calls.extend(response.tool_calls)
        results = await _execute_all(state.tools, response.tool_calls, state.executor, parallel)
        assistant_turn = Message(
            role=Role.ASSISTANT,
            parts=tuple(ToolCallPart(call=call) for call in response.tool_calls),
        )
        conversation = (*conversation, assistant_turn, Message(role=Role.USER, parts=results))
        if round_number >= max_rounds:
            # Still wants a tool call, but the round cap is spent: report exhaustion, not success.
            return _loop_result(response, round_number, calls, usage, is_exhausted=True)


async def _prompted_protocol(
    state: _LoopState, request: LLMRequest, max_rounds: int
) -> ToolLoopResult:
    """Run the prompted tool protocol: a preamble, fenced blocks parsed back out, loop.

    See `_native_protocol`'s docstring for why this is an unconditional loop with every exit
    inside the body, rather than a post-loop read of the last round's response.
    """
    preamble = Message.text(Role.USER, render_tool_preamble(state.tools))
    conversation = (*request.messages, preamble)
    usage = Usage(input_tokens=0, output_tokens=0)
    calls: list[ToolCall] = []
    call_counter = 0
    round_number = 0
    while True:
        round_number += 1
        attempt = request.model_copy(update={"messages": conversation, "tools": ()})
        response = await state.gate.complete(state.current, attempt)
        usage = usage + response.usage
        if response.stop_reason is StopReason.REFUSAL:
            raise RefusedError(
                state.current.provider.name, response.reasoning_summary or "no reason given"
            )
        blocks = extract_tool_blocks(response.text)
        if not blocks:
            return _loop_result(response, round_number, calls, usage, is_exhausted=False)
        round_calls: list[ToolCall] = []
        for block in blocks:
            call_counter += 1
            round_calls.append(_parse_tool_block(block, f"call_{call_counter}"))
        calls.extend(round_calls)
        results = await _execute_all(
            state.tools, tuple(round_calls), state.executor, parallel=False
        )
        assistant_turn = Message.text(Role.ASSISTANT, response.text)
        result_turn = _prompted_result_message(round_calls, results)
        conversation = (*conversation, assistant_turn, result_turn)
        if round_number >= max_rounds:
            # Still made calls, but the round cap is spent: report exhaustion, not success.
            return _loop_result(response, round_number, calls, usage, is_exhausted=True)


def _loop_result(
    response: LLMResponse,
    round_number: int,
    calls: list[ToolCall],
    usage: Usage,
    *,
    is_exhausted: bool,
) -> ToolLoopResult:
    """Build the ToolLoopResult both protocols return, from their last response."""
    return ToolLoopResult(
        final_text=response.text,
        rounds=round_number,
        calls=tuple(calls),
        usage=usage,
        stop_reason=response.stop_reason,
        is_exhausted=is_exhausted,
    )


async def _execute_all(
    tools: tuple[ToolDefinition, ...],
    calls: tuple[ToolCall, ...],
    executor: ToolExecutor,
    parallel: bool,
) -> tuple[ToolResultPart, ...]:
    """Validate then execute every call in `calls`, concurrently when `parallel` else in order."""
    errors_per_call = [_validate_call(tools, call) for call in calls]
    pairs = tuple(zip(calls, errors_per_call, strict=True))
    if parallel:
        return tuple(await asyncio.gather(*(_result_for_call(c, e, executor) for c, e in pairs)))
    results: list[ToolResultPart] = []
    for call, errors in pairs:
        results.append(await _result_for_call(call, errors, executor))
    return tuple(results)


def _validate_call(tools: tuple[ToolDefinition, ...], call: ToolCall) -> tuple[str, ...]:
    """Return `call`'s validation errors: unknown-tool, or its schema's own findings."""
    tool = next((t for t in tools if t.name == call.name), None)
    if tool is None:
        return (f"no tool named {call.name!r} was offered.",)
    return validate_arguments(tool.parameters, call.arguments)


async def _result_for_call(
    call: ToolCall, errors: tuple[str, ...], executor: ToolExecutor
) -> ToolResultPart:
    """Return an is_error result for an invalid `call` without executing it; else run it."""
    if errors:
        return ToolResultPart(call_id=call.id, content="; ".join(errors), is_error=True)
    return await executor.execute(call)


def _prompted_result_message(calls: list[ToolCall], results: tuple[ToolResultPart, ...]) -> Message:
    """Render every call's result as one line each, in one user text Message."""
    lines = [_render_result_line(call, result) for call, result in zip(calls, results, strict=True)]
    return Message.text(Role.USER, "\n".join(lines))


def _render_result_line(call: ToolCall, result: ToolResultPart) -> str:
    """Render one call's result line: 'Result for <id> (<name>): <content>', errors marked."""
    marker = "ERROR: " if result.is_error else ""
    return f"Result for {call.id} ({call.name}): {marker}{result.content}"


def _parse_tool_block(block: str, call_id: str) -> ToolCall:
    """Parse one fenced ```tool``` block into a ToolCall, synthesising `call_id`.

    Never raises: a block that is not valid JSON, or lacks a string `name`/object `arguments`,
    becomes a call with an empty name, which `_validate_call` then reports as "no tool named ''"
    -- untrusted model output gets a corrective is_error result, never a crash.
    """
    name, arguments = _decode_tool_block(block)
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _decode_tool_block(block: str) -> tuple[str, JsonObject]:
    """Best-effort decode of one fenced block's `{"name": ..., "arguments": {...}}` JSON."""
    try:
        decoded = json.loads(block)
    except json.JSONDecodeError:
        return "", {}
    if not isinstance(decoded, dict):
        return "", {}
    name = decoded.get("name")
    arguments = decoded.get("arguments")
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return "", {}
    return name, arguments
