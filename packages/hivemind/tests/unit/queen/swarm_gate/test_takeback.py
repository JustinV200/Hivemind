"""Tests for hivemind.queen.swarm_gate.takeback: taking a gone Warden's tasks back.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/takeback.py (codingrules section 3). The Brood Chamber,
    memory store, ledger and trail are the real in-memory classes; a task is placed on a device's
    Cell the way the dispatcher leaves it, then taken back.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.takeback for the module under test.
    - hivemind.queen.dispatcher.ready.dispatch for how a taken-back task is placed again.
"""

from __future__ import annotations

import pytest
from builders.forage import make_grant
from builders.isolation import place_running
from builders.memory import make_handoff
from builders.swarm import GateRig, place_on_device
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import InvalidTransitionError, TaskStatus
from hivemind.forage.grant_state import GrantState
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import take_back, tasks_on
from waggle.ids import TaskId, WardenId
from waggle.messages import HandoffRef


async def _decided(rig: GateRig) -> list[dict[str, object]]:
    """The payload of every `queen.decided` on the Queen's trail, oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [dict(event.payload) for event in events]


async def _checkpoint(rig: GateRig, task_id: TaskId, warden_id: WardenId) -> HandoffRef:
    """Write a Handoff for `task_id` into the Queen's memory store; its reference."""
    deps = rig.queen_deps
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    return await write_checkpoint(make_handoff(deps.clock, written_by=warden_id), task_id, ctx)


async def test_a_running_task_goes_back_to_pending_and_the_decision_is_recorded(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        taken = await take_back(rig.queen_deps, placed.node, "revoked")

        assert taken == (placed.task.id,)
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING  # Its goal kept, ready to be placed again.
        assert task.warden_id is None and task.cell_id is None
        [decision] = await _decided(rig)
        assert decision["reason"] == "tasks_taken_back" and decision["detail"] == "revoked"
        assert decision["task_ids"] == [placed.task.id]


async def test_the_newest_handoff_is_noted_so_the_next_placement_resumes_from_it(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        ref = await _checkpoint(rig, placed.task.id, placed.node.warden_id)

        await take_back(rig.queen_deps, placed.node, "grace_over")

        assert rig.queen_deps.dispatch.resumes.take(placed.task.id) == ref
        assert rig.queen_deps.dispatch.resumes.take(placed.task.id) is None  # Used up.


async def test_a_task_with_no_handoff_is_taken_back_to_start_fresh(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        await take_back(rig.queen_deps, placed.node, "failed")

        assert rig.queen_deps.dispatch.resumes.take(placed.task.id) is None


async def test_an_assigned_task_that_never_started_is_taken_back_too(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        deps, tier = rig.queen_deps, placed.link.cell.comb_shield
        [other] = await deps.chamber.submit(make_graph_draft({"root": ()}))
        await deps.chamber.assign(
            other.id, placed.node.warden_id, placed.node.cell_id, "Placed.", bound_tier=tier
        )

        taken = await take_back(deps, placed.node, "rebuilt")

        assert set(taken) == {placed.task.id, other.id}
        assert (await deps.chamber.get(other.id)).status is TaskStatus.PENDING


async def test_a_blocked_task_has_its_question_withdrawn_first(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        deps = rig.queen_deps
        await deps.chamber.ask(placed.task.id, placed.node.warden_id, "Which one?")

        taken = await take_back(deps, placed.node, "revoked")

        assert taken == (placed.task.id,)
        task = await deps.chamber.get(placed.task.id)
        # The Warden that would have been given the Answer is gone, so the Question is withdrawn.
        assert task.status is TaskStatus.PENDING and task.pending_question_id is None


async def test_a_paused_task_is_taken_back_as_well(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        await rig.queen_deps.chamber.pause(placed.task.id, "Clustering.")

        taken = await take_back(rig.queen_deps, placed.node, "grace_over")

        assert taken == (placed.task.id,)
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING


async def test_a_task_that_has_ended_is_left_exactly_as_it_is(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        await rig.queen_deps.chamber.cancel(placed.task.id, "The operator cancelled the goal.")

        taken = await take_back(rig.queen_deps, placed.node, "revoked")

        assert taken == ()
        assert await _decided(rig) == []  # Nothing was decided, so nothing is said.
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.CANCELLED


async def test_a_task_on_another_device_is_not_touched(rig: GateRig) -> None:
    async with rig.serving():
        mine, theirs = await place_on_device(rig), await place_on_device(rig)

        taken = await take_back(rig.queen_deps, mine.node, "revoked")

        assert taken == (mine.task.id,)
        task = await rig.queen_deps.chamber.get(theirs.task.id)
        assert task.status is TaskStatus.RUNNING and task.warden_id == theirs.node.warden_id


async def test_only_the_named_tasks_are_taken_and_the_wardens_grants_stay(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        keep = await place_running(rig.queen_deps, placed.link)
        grant = make_grant(
            GrantState.ACTIVE, rig.clock, holder=placed.node.warden_id, cell_id=placed.node.cell_id
        )
        await rig.queen_deps.ledger.record_grant(grant)

        taken = await take_back(rig.queen_deps, placed.node, "resume_failed", only={placed.task.id})

        assert taken == (placed.task.id,)
        assert (await rig.queen_deps.chamber.get(keep.id)).status is TaskStatus.RUNNING
        assert rig.queen_deps.ledger.grants_for(placed.node.warden_id) == (grant,)


async def test_the_wardens_live_grants_are_given_back_to_the_pool(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        deps = rig.queen_deps
        issued = make_grant(
            GrantState.ISSUED, rig.clock, holder=placed.node.warden_id, cell_id=placed.node.cell_id
        )
        active = make_grant(
            GrantState.ACTIVE, rig.clock, holder=placed.node.warden_id, cell_id=placed.node.cell_id
        )
        await deps.ledger.record_grant(issued)
        await deps.ledger.record_grant(active)

        await take_back(deps, placed.node, "revoked")

        assert deps.ledger.grants_for(placed.node.warden_id) == ()  # Nothing draws on them.
        revoked = await deps.trail.query(TrailQuery(kind="forage.revoked"))
        assert len(revoked) == 2


async def test_a_task_that_moved_by_itself_is_skipped_and_the_rest_go(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        other = await place_running(rig.queen_deps, placed.link)
        chamber = rig.queen_deps.chamber
        unassign = chamber.unassign

        async def racing(task_id: str, reason: str) -> object:
            if task_id == placed.task.id:
                # It ended while the Queen was deciding about the others.
                raise InvalidTransitionError(TaskStatus.CANCELLED, TaskStatus.PENDING, task_id)
            return await unassign(task_id, reason)  # type: ignore[arg-type]

        monkeypatch.setattr(chamber, "unassign", racing)

        taken = await take_back(rig.queen_deps, placed.node, "revoked")

        assert taken == (other.id,)


async def test_tasks_on_lists_the_unfinished_tasks_placed_on_the_devices_cell(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        other = await place_on_device(rig)
        ended = await place_running(rig.queen_deps, placed.link)
        await rig.queen_deps.chamber.cancel(ended.id, "The operator cancelled the goal.")

        found = await tasks_on(rig.queen_deps, placed.node)

        assert [task.id for task in found] == [placed.task.id]
        assert other.task.id not in [task.id for task in found]
