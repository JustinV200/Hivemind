"""Tests for hivemind.queen.swarm_gate.attach: the gate's answers to its door (GateDoorEvents).

The door asks the gate who is wanted, and tells it when a Warden attached and when its connection
ended. The gate's answers are small: admit a colonized device's Warden or the Level 0 one a flow
just started, note a reconnection's replay, tell the flow waiting for its Warden, and wake the
loop; the loop, not the door, decides what a lost Warden means.

Fits into the Hive:
    Mirrors the GateDoorEvents half of src/hivemind/queen/swarm_gate/attach.py (codingrules section
    3). The colonizer, hold and hub are the rig's real ones.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.attach for the module under test.
"""

from __future__ import annotations

from builders.quarantine import settle
from builders.swarm import GateRig, attach_device_warden, make_promotion

from hivemind.queen.swarm_gate import GateDoorEvents
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import ColonizeAction

_FLOW_ROUNDS = 40_000  # Scheduling turns a flow may take to reach the wait for its Warden.


def _events(rig: GateRig, woken: list[bool]) -> GateDoorEvents:
    """The gate's answers over the rig's colonizer, hold and hub, counting wake-ups."""
    return GateDoorEvents(rig.colonizer, rig.unreachable, rig.hive.hub, lambda: woken.append(True))


async def test_a_colonized_devices_warden_is_always_wanted(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    rig.bind()

    assert _events(rig, []).admits(node)


async def test_a_nucs_warden_is_wanted_too(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.NUC)
    rig.bind()

    assert _events(rig, []).admits(node)


async def test_a_level0_devices_warden_is_wanted_only_while_a_colonization_waits_for_it(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.on_start = None  # START is answered; nobody dials.
        events = _events(rig, [])
        node = await scenario.node()
        assert not events.admits(node)  # Its Warden is on the Hive Stand.

        await scenario.start()
        await settle(lambda: ColonizeAction.START in scenario.packet.actions, _FLOW_ROUNDS)

        assert events.admits(node)  # The flow is waiting for exactly this Warden.
        await scenario.close()


async def test_an_attach_wakes_the_loop_and_a_reconnection_notes_the_replay_to_expect(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    rig.bind()
    woken: list[bool] = []
    link, _end = await attach_device_warden(rig, node)

    await _events(rig, woken).attached(node, link, reconnected=True)

    assert woken == [True]
    assert rig.hive.hub.attached_at_for_node(node.node_id) is not None  # Its outbox replays.


async def test_a_first_attach_notes_no_replay(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    rig.bind()
    woken: list[bool] = []
    link, _end = await attach_device_warden(rig, node)

    await _events(rig, woken).attached(node, link, reconnected=False)

    assert woken == [True]
    assert rig.hive.hub.attached_at_for_node(node.node_id) is None


async def test_an_ended_connection_notes_why_and_wakes_the_loop_to_hold_the_device(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        link, _end = await attach_device_warden(rig, node)
        woken: list[bool] = []

        await _events(rig, woken).ended(node, link)
        await rig.unreachable.lost(node)  # What the woken loop does.

        assert woken == [True]
        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["reason"] == "warden_link_closed"
