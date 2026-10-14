"""Tests for hivemind.queen.swarm_gate.reconcile: tasks are not orphaned when a Warden goes.

A Warden can end three ways that are not the device's link dropping: the operator revokes the
device, the operator changes its terms so its Warden is rebuilt, or its loop dies of a device error.
In each, the tasks it held must not be left running on a Warden that is no longer there. They are
taken back to PENDING (a bee that can is first made to write its Handoff), the decision is on the
trail, and the dispatcher places them afresh.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/reconcile.py (codingrules section 3), split by feature
    (14.2). The Wardens are the real class over a `FakeCellSource`; the tasks are really RUNNING.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.reconcile for the module under test.
    - hivemind.queen.swarm_gate.takeback for how the tasks are taken back.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle
from builders.swarm import GateRig, place_on_device

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel
from hivemind.pheromone import TrailQuery
from hivemind.queen.cluster import pause_on_warden
from hivemind.queen.swarm_gate.reconcile import REBUILD_PAUSE_REASON
from hivemind.swarm import NodeStatus, RuntimeLevel, SwarmError, SwarmNode
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from hivemind.wardens import Warden


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _revoke(rig: GateRig, node: SwarmNode) -> SwarmNode:
    """Revoke `node` the way the operator's command does, event and all."""
    event = rig.hive.recorder.swarm_event("swarm.revoked", node.device_id, {})
    row = await rig.hive.registry.node(node.device_id)  # Its status may have moved meanwhile.
    assert row is not None
    return await rig.hive.registry.set_status(
        node.device_id, row.status, NodeStatus.REVOKED, rig.clock.now(), event
    )


async def test_a_revoked_devices_tasks_are_taken_back_to_be_placed_afresh(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        await rig.reconciler.sync(await _revoke(rig, placed.node))

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING and task.warden_id is None
        assert ("tasks_taken_back", "revoked") in await _decisions(rig)


async def test_a_revoked_colonized_devices_warden_is_dropped_and_its_hold_ended(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        await rig.unreachable.lost(placed.node)  # A hold was running when it was revoked.
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)

        await rig.reconciler.sync(await _revoke(rig, placed.node))

        assert rig.queen.wardens == ()  # The Queen forgot its Warden...
        assert not placed.link.transport.is_connected  # ...and closed its connection.
        assert rig.unreachable.hold_of(placed.node.device_id) is None
        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING
        assert ("unreachable_decision", "replace") not in await _decisions(rig)  # No decision.


async def test_a_revocation_is_acted_on_once_however_often_the_pass_sees_it(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        revoked = await _revoke(rig, placed.node)

        for _ in range(3):
            await rig.reconciler.sync(revoked)

        taken = [d for d in await _decisions(rig) if d[0] == "tasks_taken_back"]
        assert taken == [("tasks_taken_back", "revoked")]


async def test_a_rebuilt_warden_has_its_bees_paused_first_and_its_tasks_taken_back(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused: list[tuple[str, str]] = []

    async def spy(deps: object, wardens: object, warden_id: str, reason: str) -> object:
        paused.append((warden_id, reason))
        return await pause_on_warden(deps, wardens, warden_id, reason)  # type: ignore[arg-type]

    monkeypatch.setattr("hivemind.queen.swarm_gate.reconcile.pause_on_warden", spy)
    async with rig.serving():
        placed = await place_on_device(rig)
        await rig.hive.registry.set_access(
            placed.node.device_id,
            AccessLevel.READ_ONLY,
            granted_capabilities(AccessLevel.READ_ONLY, placed.node.cell_id),
        )
        changed = await rig.hive.registry.node(placed.node.device_id)
        assert changed is not None

        await rig.reconciler.sync(changed)

        assert paused == [(placed.node.warden_id, REBUILD_PAUSE_REASON)]
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING  # Taken back, not left on a Warden that is gone.
        assert ("tasks_taken_back", "rebuilt") in await _decisions(rig)
        rebuilt = rig.wardens.get(placed.node.device_id)
        assert rebuilt is not None and rebuilt.node.access_level is AccessLevel.READ_ONLY


async def test_a_warden_whose_loop_died_has_its_tasks_taken_back(rig: GateRig) -> None:
    rig.factory.warden_class = _DyingWarden
    async with rig.serving():
        placed = await place_on_device(rig)
        dead = rig.wardens.get(placed.node.device_id)
        assert dead is not None
        await settle(lambda: dead.has_ended)
        rig.factory.warden_class = Warden

        await rig.reconciler.sync(placed.node)

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING
        assert ("tasks_taken_back", "failed") in await _decisions(rig)
        fresh = rig.wardens.get(placed.node.device_id)
        assert fresh is not None and fresh is not dead and not fresh.has_ended


class _DyingWarden(Warden):
    """A Warden whose first tick fails the way a device going away mid-command does."""

    async def _tick(self) -> None:
        raise SwarmError("The device went away mid-command.")
