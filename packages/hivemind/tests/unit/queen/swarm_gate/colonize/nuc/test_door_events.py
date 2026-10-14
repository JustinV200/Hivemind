"""Tests for how the gate's door feeds the colonizer's desk: what a Warden says, and its going.

The door shows the gate every frame of a device Warden's connection before the Queen's reader sees
it (`DoorEvents.heard`): the colonizer notes the servers a capacity report declares and keeps the
answers to its own orders. When the connection ends (`DoorEvents.ended`) whatever an order is
waiting for stops waiting.

Fits into the Hive:
    Mirrors the GateDoorEvents half of src/hivemind/queen/swarm_gate/attach.py (codingrules
    section 3), for the Nuc rungs; the first rung's are in test_attach_events.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.attach for the module under test.
    - hivemind.queen.swarm_gate.colonize.nuc.desk for what the colonizer does with each frame.
"""

from __future__ import annotations

from builders.quarantine import settle
from builders.swarm import GateRig, capacity_frame, make_nuc_device

from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import GateDoorEvents
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import NucAction

_FLOW_ROUNDS = 40_000  # Scheduling turns a flow may take to reach the wait for its Warden.


def _events(rig: GateRig) -> GateDoorEvents:
    """The gate's answers over the rig's colonizer, hold and hub."""
    return GateDoorEvents(rig.colonizer, rig.unreachable, rig.hive.hub, lambda: None)


async def test_the_wardens_frames_go_to_the_colonizer_which_says_which_are_the_gates(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    rig.bind()

    consumed = _events(rig).heard(node, capacity_frame(rig, node, "box"))

    assert consumed is False  # A capacity report is noted, and still the Queen's.
    assert rig.colonizer.declared(node.device_id) == ("box",)


async def test_a_wardens_connection_ending_stops_the_order_waiting_on_it(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.silent.add(NucAction.PROMOTE)
        assert await rig.colonizer.request(device.node)
        await settle(lambda: device.warden.actions == [NucAction.PROMOTE], _FLOW_ROUNDS)
        [link] = rig.queen.wardens

        await _events(rig).ended(device.node, link)  # What the door says when it ends.

        await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)
        decisions = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
        assert [e.payload["detail"] for e in decisions] == ["link_lost"]
        await device.close()
