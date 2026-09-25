"""Unit tests for hivemind.workers.roles.drone.Drone: the bounded tool-loop role.

Codingrules section 8.6's degradation ladder is exercised twice here: once at full capabilities
(native tool calls) and once at `ProviderCapabilities.none()` (the prompted rung, a plain-text
model that can still complete work through fenced ```tool blocks) -- see
`test_drone_completes_via_the_prompted_rung_at_zero_capabilities`.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.llm import make_bound, make_tool_call
from builders.workers import make_assignment, make_context

from hivemind.llm import (
    FakeLLMProvider,
    ProviderCapabilities,
    Usage,
    text_response,
    tool_call_response,
)
from hivemind.llm.errors import ContextTooLongError
from hivemind.memory.overflow import MAX_OVERFLOWS, ContextOverflowError
from hivemind.pheromone import TrailQuery
from hivemind.workers.errors import WorkerCancelledError
from hivemind.workers.roles.drone import Drone
from waggle.clock import FakeClock
from waggle.messages.honey import HoneyHit, HoneyProvenance
from waggle.messages.labels import CombShieldLevel, HoneyClearance


async def test_drone_happy_path_writes_three_haiku_and_completes() -> None:
    """The Drone must write three haiku to separate scratch files and claim completion."""
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment(objective="Write three haiku to scratch, one file each.")
    calls = [
        make_tool_call(
            id=f"call_{i}",
            name="write_file",
            arguments={"path": f"haiku{i}.txt", "content": "line one\nline two\nline three"},
        )
        for i in (1, 2, 3)
    ]
    provider.script(
        tool_call_response(calls[0]),
        tool_call_response(calls[1]),
        tool_call_response(calls[2]),
        text_response("Wrote three haiku to scratch."),
    )

    outcome = await Drone().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert outcome.handoff is None
    assert {artifact.path for artifact in outcome.artifacts} == {
        "haiku1.txt",
        "haiku2.txt",
        "haiku3.txt",
    }
    for artifact in outcome.artifacts:
        assert artifact.size_bytes > 0


async def test_drone_happy_path_records_capping_events_on_the_trail() -> None:
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment()
    call = make_tool_call(name="write_file", arguments={"path": "haiku1.txt", "content": "a\nb\nc"})
    provider.script(tool_call_response(call), text_response("Done."))

    await Drone().run(ctx, assignment, resume_from=None)

    events = await ctx.trail.query(TrailQuery(family="capping", limit=100))
    assert events  # at least capping.proposed/checked/capped/applied/verified were recorded
    assert any(event.kind == "capping.verified" for event in events)


async def test_drone_outcome_and_telemetry_carry_what_its_priced_rounds_cost() -> None:
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    call = make_tool_call(name="write_file", arguments={"path": "haiku1.txt", "content": "a\nb\nc"})
    provider.script(
        tool_call_response(call).model_copy(
            update={"usage": Usage(input_tokens=10, output_tokens=1, cost_usd=0.25)}
        ),
        text_response("Done.", usage=Usage(input_tokens=20, output_tokens=2, cost_usd=0.5)),
    )

    outcome = await Drone().run(ctx, make_assignment(), resume_from=None)

    assert outcome.spend_usd == 0.75
    assert ctx.telemetry.snapshot().spend == 0.75


async def test_drone_completes_via_the_prompted_rung_at_zero_capabilities() -> None:
    """Codingrules 8.6: a plain-text model still completes work, through the prompted rung.

    Brief section 7's "zero capabilities" path -- `ProviderCapabilities.none()` offers no native
    tool-call protocol, so `hivemind.llm.run_tool_loop` falls back to the prompted protocol: a
    fenced ```tool block the model writes itself, parsed back out by
    `hivemind.llm.ladders.extraction.extract_tool_blocks`.
    """
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment(objective="Write one haiku to scratch/haiku.txt.")
    tool_block = (
        '```tool\n{"name": "write_file", '
        '"arguments": {"path": "haiku.txt", "content": "l1\\nl2\\nl3"}}\n```'
    )
    provider.script(
        text_response(f"I will write the file now.\n\n{tool_block}"),
        text_response("Done."),
    )

    outcome = await Drone().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert [artifact.path for artifact in outcome.artifacts] == ["haiku.txt"]


async def test_drone_hands_off_when_telemetry_crosses_the_threshold() -> None:
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider), handoff_threshold=0.5)
    # Force the tracker past the threshold before the loop ever calls a tool.
    ctx.telemetry.record_tokens(ctx.bound.context_window, ctx.bound.context_window)
    assignment = make_assignment()
    call = make_tool_call(name="write_file", arguments={"path": "haiku1.txt", "content": "a\nb\nc"})
    provider.script(tool_call_response(call))

    outcome = await Drone().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is False
    assert outcome.handoff is not None
    handoff = outcome.handoff
    assert handoff.goal
    assert handoff.progress
    assert handoff.written_by == str(ctx.worker_id)
    assert handoff.task_id == assignment.task_id


async def test_pause_is_awaited_between_tool_calls() -> None:
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment()
    ctx.telemetry.pause()
    call = make_tool_call(name="write_file", arguments={"path": "haiku1.txt", "content": "a\nb\nc"})
    provider.script(tool_call_response(call), text_response("Done."))

    task = asyncio.ensure_future(Drone().run(ctx, assignment, resume_from=None))
    await asyncio.sleep(0)
    assert not task.done()  # blocked on wait_if_paused, never reached the tool call

    ctx.telemetry.resume()
    outcome = await task

    assert outcome.claimed is True


async def test_drone_shrinks_and_retries_on_context_too_long() -> None:
    # Roadmap step 4.4: a ContextTooLongError never crashes a Drone attempt.
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment()
    call = make_tool_call(name="write_file", arguments={"path": "haiku1.txt", "content": "a\nb\nc"})
    provider.script(
        ContextTooLongError("fake", window=1_000, requested=2_000),
        tool_call_response(call),
        text_response("Done."),
    )

    outcome = await Drone().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    events = await ctx.trail.query(TrailQuery(family="memory"))
    assert [e.kind for e in events] == ["memory.overflow"]


async def test_drone_raises_context_overflow_after_max_overflows() -> None:
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment()
    for _ in range(MAX_OVERFLOWS + 1):
        provider.script(ContextTooLongError("fake", window=1_000, requested=2_000))

    with pytest.raises(ContextOverflowError) as exc_info:
        await Drone().run(ctx, assignment, resume_from=None)

    assert exc_info.value.attempts == MAX_OVERFLOWS


async def test_cancel_raises_worker_cancelled_error() -> None:
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    assignment = make_assignment()
    ctx.telemetry.cancel_requested = True
    call = make_tool_call(name="write_file", arguments={"path": "haiku1.txt", "content": "a\nb\nc"})
    provider.script(tool_call_response(call))

    with pytest.raises(WorkerCancelledError):
        await Drone().run(ctx, assignment, resume_from=None)


async def test_drone_shows_its_assignments_honey_to_the_model_as_retrieved_data() -> None:
    # Roadmap 7.7: the Queen's pre-check hits ride on TaskAssign.honey into the RETRIEVED section.
    provider = FakeLLMProvider()
    ctx = make_context(bound=make_bound(provider=provider))
    hit = HoneyHit(
        honey_ref="/hive/honey_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        title="Where the report goes",
        excerpt="Reports for this Cell are written under scratch/.",
        score=0.8,
        scope="hive",
        clearance=HoneyClearance.C1,
        origin_tier=CombShieldLevel.MEADOW,
        provenance=HoneyProvenance(
            task_id=None, cell_id=None, bee=None, observed_at=FakeClock().now()
        ),
    )
    assignment = make_assignment(honey=(hit,))
    provider.script(text_response("Done."))

    await Drone().run(ctx, assignment, resume_from=None)

    system = provider.calls[0].system
    assert system is not None
    assert system.index("<<<retrieved>>>\n") < system.index(hit.excerpt)
    assert system.index(hit.excerpt) < system.index("<<<end retrieved>>>")
