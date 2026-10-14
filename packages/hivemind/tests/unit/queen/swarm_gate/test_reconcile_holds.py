"""Tests for hivemind.queen.swarm_gate.reconcile: a device above Level 0, and a request for a rung.

A device whose Warden dialled in from the device is reachable while that Warden's link is open;
when it is not, the device's tasks are held. A device a colonization owns is left to its flow. A
pending `desired_level` on a reachable device is handed to the colonizer. These are the parts of
the reconcile pass that are new with the runtime ladder; the Level 0 rules are in test_reconcile.py.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/reconcile.py (codingrules section 3), split by feature
    (14.2). The registry, hub, Queen and Wardens are the real classes; a device above Level 0 has
    its Warden's link attached to the Queen directly.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.reconcile for the module under test.
"""

from __future__ import annotations

from builders.cells import make_capabilities
from builders.swarm import (
    GRACE_S,
    GateRig,
    ScriptedPacket,
    attach_device_warden,
    make_promotion,
    place_on_device,
)

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import HoldState
from hivemind.queen.swarm_gate.unreachable import WARDEN_LINK_REASON
from hivemind.swarm import NodeStatus, RuntimeLevel, SwarmNode
from waggle.messages.swarm import ColonizeAction

_FLOW_ROUNDS = 40_000  # Scheduling turns a flow may take.


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_a_colonized_device_with_its_wardens_link_open_is_reachable(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)

        await rig.reconciler.sync(placed.node)

        assert rig.unreachable.hold_of(placed.node.device_id) is None
        assert await rig.hive.events("swarm.node_held") == []
        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)


async def test_a_colonized_device_whose_wardens_link_has_closed_has_its_tasks_held(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        await placed.link.transport.close()  # Its Warden's connection ended.

        await rig.reconciler.sync(placed.node)

        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.HELD
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["tasks"] == 1
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.RUNNING  # Held, not failed and not moved.


class _Asked:
    """A diagnosis that notes each device it is asked to look into, and the reason it is given."""

    def __init__(self) -> None:
        self.begun: list[str] = []

    async def begin(self, node: SwarmNode, reason: str) -> None:
        self.begun.append(reason)

    async def end(self, node: SwarmNode) -> None:
        return None


async def test_a_colonized_devices_closed_link_is_looked_into_before_the_door_says_why(
    rig: GateRig,
) -> None:
    diagnosis = _Asked()
    rig.unreachable.use_diagnosis(diagnosis)
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        # A dead peer's connection reads closed to the pass when its keepalive gives up, some
        # seconds before the door's reader returns and notes the Warden's link ended: nothing is
        # noted here, as when the pass gets there first, and the device was still seen to go.
        await placed.link.transport.close()

        await rig.reconciler.sync(placed.node)

        assert diagnosis.begun == [WARDEN_LINK_REASON]
        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["reason"] == WARDEN_LINK_REASON and held.payload["tasks"] == 1


async def test_a_colonized_device_the_queen_has_no_warden_for_is_held_but_not_looked_into(
    rig: GateRig,
) -> None:
    diagnosis = _Asked()
    rig.unreachable.use_diagnosis(diagnosis)
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)  # No Warden has dialled in.

        await rig.reconciler.sync(node)

        assert rig.unreachable.hold_of(node.device_id) is HoldState.HELD
        assert diagnosis.begun == []  # Nobody saw it go.


async def test_a_colonized_device_the_queen_has_no_warden_for_is_held_too(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)  # After a Hive restart: none yet.

        await rig.reconciler.sync(node)

        assert rig.unreachable.hold_of(node.device_id) is HoldState.HELD


async def test_a_held_colonized_device_is_freed_when_its_warden_is_back(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        await placed.link.transport.close()
        await rig.reconciler.sync(placed.node)
        rig.clock.advance(GRACE_S / 2)

        await attach_device_warden(rig, placed.node)  # It dials in again.

        await rig.reconciler.sync(placed.node)

        assert rig.unreachable.hold_of(placed.node.device_id) is None
        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.RUNNING and task.warden_id == placed.node.warden_id


async def test_a_device_a_flow_owns_is_left_alone_by_the_pass(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.silent.add(ColonizeAction.PREPARE)  # The flow waits on the packet.
        served = rig.wardens.get(scenario.device_id)
        await scenario.start()

        await rig.reconciler.sync(await scenario.node())

        assert rig.wardens.get(scenario.device_id) is served  # Not stopped, not served again.
        assert rig.unreachable.hold_of(scenario.device_id) is None
        await scenario.close()


async def test_a_reachable_device_that_asks_for_a_rung_it_cannot_carry_is_answered(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol(capabilities=make_capabilities(python_version="3.11.9"))
        await rig.connect(node)
        await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.COLONIZED)
        row = await rig.hive.registry.node(node.device_id)
        assert row is not None

        await rig.reconciler.sync(row)

        assert ("rung_refused", "python_too_old") in await _decisions(rig)
        answered = await rig.hive.registry.node(node.device_id)
        assert answered is not None and answered.desired_level is None
        assert rig.wardens.get(node.device_id) is not None  # And it is served as before.


async def test_a_reachable_device_that_asks_for_a_rung_it_can_carry_has_a_flow_started(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        await rig.reconciler.sync(await scenario.node())
        await scenario.finish()

        node = await scenario.node()
        assert node.level is RuntimeLevel.COLONIZED
        assert scenario.packet.actions == [ColonizeAction.PREPARE, ColonizeAction.START]
        await scenario.close()


async def test_a_colonized_device_that_asks_to_go_down_while_working_is_refused(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        packet = ScriptedPacket(placed.device, rig.clock, rig.hive.voice.hive_id)
        await rig.hive.registry.set_desired_level(placed.node.device_id, RuntimeLevel.GATEWAY)
        row = await rig.hive.registry.node(placed.node.device_id)
        assert row is not None

        await rig.reconciler.sync(row)

        assert ("rung_refused", "warden_busy") in await _decisions(rig)
        assert packet.orders == []
        await packet.close()


async def test_a_device_that_returns_after_its_tasks_were_taken_is_served_afresh(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        await rig.disconnect(placed.node)
        await rig.reconciler.sync(placed.node)
        rig.clock.advance(GRACE_S)
        await rig.reconciler.sync(placed.node)  # The grace ended: tasks taken, Warden let go.
        assert rig.wardens.get(placed.node.device_id) is None
        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.REPLACED

        await rig.connect(placed.node)  # The device is back.
        await rig.reconciler.sync(placed.node)

        assert rig.wardens.get(placed.node.device_id) is not None  # A fresh Level 0 Warden.
        assert rig.unreachable.hold_of(placed.node.device_id) is None
        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.PENDING  # Waiting to be placed again.


async def test_a_node_the_registry_calls_unreachable_but_whose_warden_is_up_is_not_held(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        silent = await rig.hive.registry.set_status(
            placed.node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
        )  # Its packet is quiet; its Warden is working.

        await rig.reconciler.sync(silent)

        assert rig.unreachable.hold_of(placed.node.device_id) is None
