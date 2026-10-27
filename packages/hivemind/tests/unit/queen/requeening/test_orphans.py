"""Tests for hivemind.queen.requeening.orphans: taking back what a dead Queen's Wardens held.

Each placed state a gone Warden can leave a task in comes back as a fresh attempt, resuming from
its newest Handoff (the hint its next placement reads), a waiting question withdrawn; one bound to
a Night Veil Cell is cancelled instead. A PAUSED task counts as Clustering's only when neither a
quarantine since it last resumed nor an isolation of its Cell holds it.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/orphans.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.orphans for the module under test.
"""

from __future__ import annotations

import pytest
from builders.memory import make_handoff
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.pheromone import WardenEvent
from hivemind.queen.deps import QueenDeps
from hivemind.queen.requeening import clustering_paused, take_back
from waggle.clock import FakeClock
from waggle.ids import WardenId, new_cell_id, new_event_id, new_warden_id


async def _placed(deps: QueenDeps, state: str) -> Task:
    """One task placed on a Warden that is gone, left in `state`, with a Handoff written."""
    (task,) = await deps.chamber.submit(make_graph_draft({"poem": ()}))
    tier = CombShieldLevel.NIGHT_VEIL if state == "NIGHT_VEIL" else CombShieldLevel.MEADOW
    gone: WardenId = new_warden_id(deps.clock)
    cell = new_cell_id(deps.clock)
    await deps.chamber.assign(task.id, gone, cell, "Placed.", bound_tier=tier)
    await deps.chamber.start(task.id)
    if state == "BLOCKED":
        await deps.chamber.ask(task.id, asked_by=gone, text="Which?", clearance=HoneyClearance.C1)
    elif state == "PAUSED":
        await deps.chamber.pause(task.id, "Clustering: provider 'fake' unavailable.")
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    await write_checkpoint(make_handoff(deps.clock), task.id, ctx)
    return await deps.chamber.get(task.id)


@pytest.mark.parametrize(
    ("state", "ends", "counted"),
    [
        ("RUNNING", TaskStatus.PENDING, "given_back"),
        ("BLOCKED", TaskStatus.PENDING, "given_back"),
        ("PAUSED", TaskStatus.PENDING, "paused_replaced"),
        ("NIGHT_VEIL", TaskStatus.CANCELLED, "night_veil_cancelled"),
    ],
)
async def test_a_gone_wardens_task_comes_back_from_its_handoff_or_ends_with_its_night_veil_cell(
    state: str, ends: TaskStatus, counted: str
) -> None:
    deps, _link, _end = make_queen_deps()
    task = await _placed(deps, state)

    counts = await take_back(deps, [task])

    after = await deps.chamber.get(task.id)
    assert after.status is ends
    assert getattr(counts, counted) == 1
    assert await deps.chamber.pending_questions() == ()  # A waiting question is withdrawn.
    hint = deps.dispatch.resumes.take(task.id)
    # A fresh attempt resumes from the newest Handoff; a cancelled one is never placed again.
    assert (hint is not None) is (ends is TaskStatus.PENDING)


@pytest.mark.parametrize(
    ("hold", "is_clusterings"),
    [("none", True), ("quarantine", False), ("quarantine_then_resumed", True)],
)
async def test_a_pause_is_clusterings_unless_a_quarantine_since_its_last_resume_holds_it(
    hold: str, is_clusterings: bool
) -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    task = await _placed(deps, "PAUSED")
    if hold.startswith("quarantine"):
        await deps.trail.record(_intervened(deps, task))
    if hold == "quarantine_then_resumed":
        clock.advance(1.0)
        await deps.chamber.resume(task.id, "A judge cleared it.")
        await deps.chamber.pause(task.id, "Clustering: provider 'fake' unavailable.")
        task = await deps.chamber.get(task.id)

    held = await clustering_paused(deps, [task])

    assert (held == (task,)) is is_clusterings


def _intervened(deps: QueenDeps, task: Task) -> WardenEvent:
    """The `warden.intervened` a quarantine of `task`'s bee records."""
    return WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor="system",
        kind="warden.intervened",
        subject_id=task.warden_id or "",
        payload={"task_id": task.id, "handoff_event_id": new_event_id(deps.clock)},
    )
