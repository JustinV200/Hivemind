"""Tests for hivemind.queen.swarm_gate.unreachable.devices: what is decided when the grace ends.

Fits into the Hive:
    Mirrors the second half of src/hivemind/queen/swarm_gate/unreachable/devices.py (codingrules
    section 3, split by feature per 14.2): the timeline of a hold against a FakeClock, and the
    verdict each rung gets. A task is really RUNNING on a Warden the Queen really holds.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.unreachable.devices for the module under test.
    - tests.unit.queen.swarm_gate.unreachable.test_devices for beginning and cancelling a hold.
"""

from __future__ import annotations

import pytest
from builders.swarm import (
    GRACE_S,
    MAX_OFFLINE_S,
    GateRig,
    PlacedDevice,
    attach_device_warden,
    gate_terms,
    place_on_device,
)

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.swarm_gate import GateContext, HoldState, UnreachableDevices
from hivemind.queen.swarm_gate.unreachable import devices as devices_module
from hivemind.queen.swarm_gate.unreachable.effects import cancel_abandoned
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.ids import TaskId


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _lost_past_the_grace(rig: GateRig, placed: PlacedDevice) -> None:
    """Lose the device, let the whole grace pass, and look at it again as the next pass does."""
    await rig.unreachable.lost(placed.node)
    rig.clock.advance(GRACE_S)
    await rig.unreachable.lost(placed.node)


async def test_a_level0_devices_tasks_are_taken_back_when_the_grace_ends(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        await _lost_past_the_grace(rig, placed)

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING  # Back to be placed afresh, its goal kept.
        assert task.warden_id is None and task.cell_id is None
        assert rig.wardens.get(placed.node.device_id) is None  # Its Warden was let go.
        assert rig.queen.wardens == ()
        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.REPLACED
        # Still held: nothing can reach the Cell anyway, and `found` opens it only once a
        # returning Warden has been told which of its tasks were placed elsewhere.
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        assert await _decisions(rig) == [
            ("unreachable_decision", "replace"),
            ("tasks_taken_back", "grace_over"),
        ]


async def test_the_decision_is_made_once_however_often_the_pass_looks(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        await _lost_past_the_grace(rig, placed)

        await rig.unreachable.lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.lost(placed.node)

        assert len(await _decisions(rig)) == 2  # The decision and the take-back, once each.


async def test_a_colonized_devices_tasks_are_taken_back_and_its_dead_link_dropped(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        assert placed.end is not None

        await rig.unreachable.warden_lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.warden_lost(placed.node)

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING
        assert rig.queen.wardens == ()  # The Queen forgot the Warden's link.
        assert not placed.link.transport.is_connected  # And closed its connection.
        assert await _decisions(rig) == [
            ("unreachable_decision", "replace"),
            ("tasks_taken_back", "grace_over"),
        ]


async def test_a_grace_of_zero_decides_at_once(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        quick = UnreachableDevices(
            rig.wardens,
            rig.hive.registry,
            rig.hive.recorder,
            gate_terms(unreachable_grace_s=0.0),
        )
        quick.bind(GateContext(rig.queen, rig.queen_deps))

        await quick.lost(placed.node)

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING


async def test_a_nucs_tasks_keep_waiting_past_the_grace(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.NUC)

        await rig.unreachable.warden_lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.warden_lost(placed.node)

        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.WAITING
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.RUNNING and task.warden_id == placed.node.warden_id
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)  # Still held, not freed.
        assert await _decisions(rig) == [("unreachable_decision", "wait")]


async def test_a_nuc_is_treated_like_the_others_once_the_offline_limit_ends(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.NUC)
        await rig.unreachable.warden_lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.warden_lost(placed.node)

        rig.clock.advance(MAX_OFFLINE_S - GRACE_S - 1.0)
        await rig.unreachable.warden_lost(placed.node)
        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.WAITING  # Not yet.

        rig.clock.advance(1.0)
        await rig.unreachable.warden_lost(placed.node)

        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.REPLACED
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING
        assert await _decisions(rig) == [
            ("unreachable_decision", "wait"),
            ("tasks_taken_back", "max_offline"),
        ]


async def test_a_nuc_that_returns_while_waited_for_keeps_its_work(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.NUC)
        await rig.unreachable.warden_lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.warden_lost(placed.node)

        await rig.unreachable.found(placed.node)

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.RUNNING and task.warden_id == placed.node.warden_id
        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        assert await _decisions(rig) == [
            ("unreachable_decision", "wait"),
            ("hold_released", "reconnected"),
        ]


async def test_a_warden_that_returns_after_its_tasks_were_taken_is_told_to_cancel_them(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        await rig.unreachable.warden_lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.warden_lost(placed.node)
        _link, end = await attach_device_warden(rig, placed.node)  # It dials in again.

        await rig.unreachable.found(placed.node)

        cancel = await end.wait_for_task_cancel()
        assert cancel.task_id == placed.task.id  # So nothing runs twice.
        assert ("hold_released", "after_replacement") in await _decisions(rig)


async def test_a_returning_warden_is_told_what_to_cancel_before_its_cell_may_take_work_again(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    held_at_cancel: list[bool] = []

    async def spy(
        context: GateContext, wardens: DeviceWardens, node: SwarmNode, abandoned: tuple[TaskId, ...]
    ) -> None:
        held_at_cancel.append(rig.queen_deps.dispatch.held.is_held(node.cell_id))
        await cancel_abandoned(context, wardens, node, abandoned)

    monkeypatch.setattr(devices_module, "cancel_abandoned", spy)  # The name `found` looks up.
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        await rig.unreachable.warden_lost(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.warden_lost(placed.node)  # Taken back: PENDING, with a resume hint.
        _link, end = await attach_device_warden(rig, placed.node)  # It dials in again.

        # The attach opened nothing, so the Queen's tick, woken by the link's first frames, cannot
        # place the very task the gate is about to tell this Warden to cancel.
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        await dispatch_ready(rig.queen_deps, rig.queen.wardens)
        assert (await rig.queen_deps.chamber.get(placed.task.id)).status is TaskStatus.PENDING

        await rig.unreachable.found(placed.node)

        assert held_at_cancel == [True]  # The cancels went while the Cell was still closed...
        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)  # ...then it opened.
        assert (await end.wait_for_task_cancel()).task_id == placed.task.id
