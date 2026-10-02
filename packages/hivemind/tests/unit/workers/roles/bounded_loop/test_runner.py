"""Unit tests for hivemind.workers.roles.bounded_loop.runner: what a handed-off attempt records.

Phase 6 handoff item 4.5.2: the handoff branch of `run_bounded_loop` used to record none of its
attempt's usage, so every call a handed-off attempt made was lost from its spend. These tests pin
the fix and its one constraint: the spend is booked, but the token gauge `should_hand_off` reads
is left alone, so the resumed attempt does not hand off again before its first tool call.
"""

from __future__ import annotations

from builders.llm import make_bound, make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.llm import FakeLLMProvider, LLMResponse, Usage, text_response, tool_call_response
from hivemind.workers.roles.drone import Drone

_HANDOFF_THRESHOLD = 0.5  # Any fraction works; the tests push usage past it on purpose.


def _write_call(index: int, usage: Usage | None = None) -> LLMResponse:
    """Return a tool-call response writing one scratch file, at `usage` when one is given."""
    call = make_tool_call(
        id=f"call_{index}",
        name="write_file",
        arguments={"path": f"note{index}.txt", "content": "a\nb\nc"},
    )
    response = tool_call_response(call)
    return response if usage is None else response.model_copy(update={"usage": usage})


async def test_run_bounded_loop_books_a_handed_off_attempts_spend() -> None:
    # The Warden asked for a checkpoint (Intervene), so the first tool call hands off; the round
    # that proposed it was priced, and that money was spent whether or not the call ran.
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider), handoff_threshold=_HANDOFF_THRESHOLD)
    ctx.telemetry.handoff_requested = True
    priced = Usage(input_tokens=10, output_tokens=1, cost_usd=0.25)
    provider.script(_write_call(1, priced))

    outcome = await Drone().run(ctx, make_assignment(), resume_from=None)

    assert outcome.claimed is False
    assert outcome.spend_usd == 0.25
    assert ctx.telemetry.snapshot().spend == 0.25


async def test_run_bounded_loop_handoff_leaves_the_token_gauge_for_the_resumed_attempt() -> None:
    # A handed-off attempt that filled its whole window: were its tokens recorded on the shared
    # tracker, the resumed attempt (same tracker, AttemptManager.start resets only the flag)
    # would hand off again at its first tool call, forever.
    provider = FakeLLMProvider()
    bound = make_bound(provider=provider)
    ctx = make_context(bound=bound, handoff_threshold=_HANDOFF_THRESHOLD)
    ctx.telemetry.handoff_requested = True
    full = Usage(input_tokens=bound.context_window, output_tokens=1, cost_usd=0.25)
    provider.script(_write_call(1, full))
    first = await Drone().run(ctx, make_assignment(), resume_from=None)
    gauge_after_handoff = ctx.telemetry.should_hand_off(_HANDOFF_THRESHOLD)
    ctx.telemetry.handoff_requested = False  # What AttemptManager.start does before a resume.
    priced = Usage(input_tokens=5, output_tokens=1, cost_usd=0.25)
    provider.script(_write_call(2, priced), text_response("Done.", usage=priced))

    resumed = await Drone().run(ctx, make_assignment(), resume_from=first.handoff)

    assert gauge_after_handoff is False
    assert resumed.claimed is True
    assert resumed.spend_usd == 0.75  # Spend adds up across both attempts; the grant paid both.
