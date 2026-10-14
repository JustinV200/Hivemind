"""Tests for hivemind.queen.swarm_gate.unreachable.effects: what a hold does to a lost device.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/unreachable/effects.py (codingrules section 3). The
    registry, Queen and Wardens are the real classes; the hold's timeline that calls these is in
    test_devices.py and test_devices_decisions.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.unreachable.effects for the module under test.
"""

from __future__ import annotations

from builders.swarm import GateRig, place_on_device

from hivemind.queen.swarm_gate import GateContext
from hivemind.queen.swarm_gate.unreachable.effects import (
    cancel_abandoned,
    event_ids,
    let_go,
    mark_unreachable,
)
from hivemind.swarm import NodeStatus, RuntimeLevel


async def test_the_ids_an_event_carries_are_ids_and_a_reason_and_nothing_else(
    rig: GateRig,
) -> None:
    node = await rig.enrol()

    assert event_ids(node, "closed") == {
        "device_id": node.device_id,
        "node_id": node.node_id,
        "cell_id": node.cell_id,
        "reason": "closed",
    }


async def test_an_online_node_is_marked_unreachable_with_its_event(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    context = GateContext(rig.queen, rig.queen_deps)

    await mark_unreachable(context, rig.hive.registry, rig.hive.recorder, node, "closed")

    row = await rig.hive.registry.node(node.device_id)
    assert row is not None and row.status is NodeStatus.UNREACHABLE
    [event] = await rig.hive.events("swarm.unreachable")
    assert event.payload["reason"] == "closed"


async def test_a_node_that_is_not_online_is_left_exactly_as_it_is(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    context = GateContext(rig.queen, rig.queen_deps)
    silent = await rig.hive.registry.set_status(
        node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
    )

    await mark_unreachable(context, rig.hive.registry, rig.hive.recorder, silent, "closed")

    assert await rig.hive.events("swarm.unreachable") == []  # Nothing to change, nothing to say.


async def test_a_node_another_writer_moved_first_is_not_marked_twice(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    context = GateContext(rig.queen, rig.queen_deps)
    await rig.hive.registry.set_status(
        node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
    )

    # The caller read the node while it was still ONLINE; the registry has moved on.
    await mark_unreachable(context, rig.hive.registry, rig.hive.recorder, node, "closed")

    assert await rig.hive.events("swarm.unreachable") == []  # It was recorded by that writer.


async def test_letting_go_of_a_warden_that_never_attached_does_nothing(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)

        await let_go(GateContext(rig.queen, rig.queen_deps), rig.wardens, node)  # No raise.

        assert rig.queen.wardens == ()


async def test_a_level0_warden_is_stopped_when_let_go(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        await let_go(GateContext(rig.queen, rig.queen_deps), rig.wardens, placed.node)

        assert rig.wardens.get(placed.node.device_id) is None
        assert rig.queen.wardens == ()


async def test_nothing_is_sent_to_a_warden_that_is_not_there_or_for_nothing_abandoned(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        assert placed.end is not None
        context = GateContext(rig.queen, rig.queen_deps)

        await cancel_abandoned(context, rig.wardens, placed.node, ())  # Nothing abandoned.
        other = await rig.enrol(level=RuntimeLevel.COLONIZED)
        await cancel_abandoned(context, rig.wardens, other, (placed.task.id,))  # No Warden.

        assert placed.end.task_cancels == []
