"""Tests for hivemind.memory.taint.clear: only a CLEAR verdict, at an allowed point, clears a label.

Roadmap step 10.6d: only a judge verdict on the taint rubric, with no shared context, clears it
(`memory.taint_cleared`, through `EnforcementPoint.TAINT_CLEAR` via the Guard's Enforcer); KEEP, a
refused clearer, a judge that cannot answer and an item too long to show whole all leave it as is.

Fits into the Hive:
    Mirrors src/hivemind/memory/taint/clear.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.taint.clear for clear_taint.
    - hivemind.memory.taint.judge for ModelTaintJudge, driven here over a FakeLLMProvider.
"""

from __future__ import annotations

import pytest
from builders.llm import make_response
from builders.memory import make_handoff
from builders.taint import TaintWorld, make_stamp, make_taint_judge, make_taint_world, queen_clearer

from hivemind.guard import CapabilitySet, PrincipalKind
from hivemind.guard.enforcer import DENIED_KIND
from hivemind.llm import TextPart
from hivemind.memory import write_checkpoint
from hivemind.memory.errors import InvalidTaintTransitionError
from hivemind.memory.taint import (
    MAX_REVIEW_CHARS,
    TAINT_CLEARED_KIND,
    ClearOutcome,
    TaintClearDeps,
    TaintClearRequest,
    TaintedKind,
    TaintScope,
    TaintState,
    TaintTarget,
    clear_taint,
    taint_memory,
)
from hivemind.pheromone import TrailQuery
from waggle.ids import EventId, new_worker_id


async def _tainted_handoff(world: TaintWorld, **handoff_fields: object) -> TaintTarget:
    """Checkpoint one Handoff by a fresh bee and taint just it; return its target."""
    bee = new_worker_id(world.clock)
    handoff = make_handoff(world.clock, written_by=bee, **handoff_fields)
    await write_checkpoint(handoff, None, world.ctx)
    # The FakeClock stands still, so "from now on" covers the checkpoint just written.
    scope = TaintScope(
        authors=frozenset({bee}), since=world.clock.now(), kinds=frozenset({TaintedKind.HANDOFF})
    )
    [target] = (await taint_memory(scope, make_stamp(), world.ctx)).tainted
    return target


def _request(world: TaintWorld, target: TaintTarget) -> TaintClearRequest:
    clearer, held = queen_clearer(world.ctx)
    return TaintClearRequest(target=target, clearer=clearer, held=held)


async def test_a_clear_verdict_clears_the_label_with_its_event() -> None:
    world = make_taint_world()
    target = await _tainted_handoff(world)
    judge, _provider = make_taint_judge("CLEAR")

    result = await clear_taint(
        _request(world, target), TaintClearDeps(judge=judge, enforcer=world.enforcer, ctx=world.ctx)
    )

    handoff, _clearance = await world.ctx.store.get_handoff(EventId(target.item_id))
    [event] = await world.trail.query(TrailQuery(kind=TAINT_CLEARED_KIND))
    assert result.outcome is ClearOutcome.CLEARED
    assert handoff.tainted == result.marker
    assert handoff.tainted is not None and handoff.tainted.state is TaintState.CLEARED
    assert handoff.tainted.cleared_event_id == event.id
    assert event.payload["tainted_by"] == handoff.tainted.event_id
    assert event.payload["rubric_id"] == "taint.v1"


async def test_a_keep_verdict_changes_nothing() -> None:
    world = make_taint_world()
    target = await _tainted_handoff(world)
    judge, _provider = make_taint_judge("KEEP")

    result = await clear_taint(
        _request(world, target), TaintClearDeps(judge=judge, enforcer=world.enforcer, ctx=world.ctx)
    )

    handoff, _clearance = await world.ctx.store.get_handoff(EventId(target.item_id))
    assert result.outcome is ClearOutcome.KEPT and result.reasons == ("Judged KEEP.",)
    assert handoff.tainted is not None and handoff.tainted.state is TaintState.TAINTED
    assert await world.trail.query(TrailQuery(kind=TAINT_CLEARED_KIND)) == ()


async def test_a_clearer_without_the_items_clearance_is_refused_before_any_model_call() -> None:
    world = make_taint_world()
    target = await _tainted_handoff(world)
    judge, provider = make_taint_judge("CLEAR")
    clearer, _held = queen_clearer(world.ctx)
    low = CapabilitySet.parse("honey:clearance:c0")
    request = TaintClearRequest(target=target, clearer=clearer, held=low)

    result = await clear_taint(
        request, TaintClearDeps(judge=judge, enforcer=world.enforcer, ctx=world.ctx)
    )

    [denied] = await world.trail.query(TrailQuery(kind=DENIED_KIND))
    assert result.outcome is ClearOutcome.REFUSED
    assert denied.payload["point"] == "taint_clear"
    assert denied.payload["principal_kind"] == PrincipalKind.QUEEN.value
    assert provider.calls == []


async def test_a_judge_that_cannot_answer_leaves_the_item_tainted() -> None:
    world = make_taint_world()
    target = await _tainted_handoff(world)
    judge, provider = make_taint_judge()
    # Nothing parseable, on every rung: the ladder exhausts itself and the judge gives no verdict.
    for _ in range(9):
        provider.script(make_response(parts=(TextPart(text="not json"),)))

    result = await clear_taint(
        _request(world, target), TaintClearDeps(judge=judge, enforcer=world.enforcer, ctx=world.ctx)
    )

    assert result.outcome is ClearOutcome.UNREVIEWABLE
    handoff, _clearance = await world.ctx.store.get_handoff(EventId(target.item_id))
    assert handoff.tainted is not None and handoff.tainted.refuses


async def test_an_item_too_long_to_show_whole_is_never_judged() -> None:
    world = make_taint_world()
    # Many long list entries push the review text past the bound without breaking Handoff caps.
    target = await _tainted_handoff(world, notes="n" * 2_000, next_steps=("s" * 500,) * 40)
    judge, provider = make_taint_judge("CLEAR")

    result = await clear_taint(
        _request(world, target), TaintClearDeps(judge=judge, enforcer=world.enforcer, ctx=world.ctx)
    )

    assert MAX_REVIEW_CHARS < 40 * 500
    assert result.outcome is ClearOutcome.UNREVIEWABLE and provider.calls == []


async def test_clearing_an_item_that_is_not_tainted_is_refused() -> None:
    world = make_taint_world()
    ref = await write_checkpoint(make_handoff(), None, world.ctx)
    judge, _provider = make_taint_judge("CLEAR")
    target = TaintTarget(kind=TaintedKind.HANDOFF, item_id=ref.event_id)

    with pytest.raises(InvalidTaintTransitionError, match="unlabelled"):
        await clear_taint(
            _request(world, target),
            TaintClearDeps(judge=judge, enforcer=world.enforcer, ctx=world.ctx),
        )
