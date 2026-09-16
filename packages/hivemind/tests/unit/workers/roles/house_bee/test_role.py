"""Tests for hivemind.workers.roles.house_bee.role: HouseBee, the Worker-protocol adapter.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/house_bee/role.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.house_bee.role for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.llm import make_bound
from builders.memory import make_handoff, make_note
from builders.workers import make_assignment, make_context

from hivemind.forage.slots import ModelSlot
from hivemind.manifest.schema.supervision import DEFAULT_HOT_WINDOW_S
from hivemind.pheromone import MemoryEvent
from hivemind.workers.roles.house_bee.role import HOUSE_BEE_HOT_WINDOW_S, HouseBee
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.messages.task import WorkerRole


async def test_house_bee_role_is_always_house_bee() -> None:
    assert HouseBee().role == WorkerRole.HOUSE_BEE


async def test_house_bee_run_claims_and_never_hands_off() -> None:
    ctx = make_context(bound=make_bound(slot=ModelSlot.RIPENER))
    assignment = make_assignment(role=WorkerRole.HOUSE_BEE)

    outcome = await HouseBee().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert outcome.handoff is None


async def test_house_bee_run_ignores_resume_from() -> None:
    ctx = make_context(bound=make_bound(slot=ModelSlot.RIPENER))
    assignment = make_assignment(role=WorkerRole.HOUSE_BEE)

    # A sweep is stateless maintenance (module docstring): passing a Handoff must not raise or
    # change the outcome shape.
    outcome = await HouseBee().run(ctx, assignment, resume_from=make_handoff())

    assert outcome.claimed is True


async def test_house_bee_run_demotes_a_note_aged_past_the_hot_window() -> None:
    clock = FakeClock()
    ctx = make_context(clock=clock, bound=make_bound(slot=ModelSlot.RIPENER))
    assignment = make_assignment(clock=clock, role=WorkerRole.HOUSE_BEE)
    # A Note written now; advancing the clock past HOUSE_BEE_HOT_WINDOW_S before running the sweep
    # is what ages it out (hivemind.memory.demote.should_demote's AGED_OUT rule).
    note = make_note(clock=clock)
    event = MemoryEvent(
        id=new_event_id(clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=clock.now(),
        actor="system",
        kind="memory.note",
        subject_id=note.id,
        payload={},
    )
    await ctx.memory.add_note(note, event)
    clock.advance(HOUSE_BEE_HOT_WINDOW_S + 1.0)

    outcome = await HouseBee().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert "demoted 1 item" in outcome.summary
    remaining = await ctx.memory.list_notes(None, note.clearance, 10)
    assert note.id not in {n.id for n in remaining}


def test_house_bee_hot_window_mirrors_the_manifest_default() -> None:
    assert HOUSE_BEE_HOT_WINDOW_S == DEFAULT_HOT_WINDOW_S
    assert timedelta(seconds=HOUSE_BEE_HOT_WINDOW_S) == timedelta(hours=4)
