"""Tests for hivemind.queen.swarm_gate.gate: the Queen's side of the Swarm, start to stop.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/gate.py (codingrules section 3). The gate is the real
    class over a real Queen, registry, hub and (unstarted-until-asked) listener; a device is a
    link that is open and silent, and the Wardens are the real class over a `FakeCellSource`.
    Time is the rig's FakeClock: the gate's interval is a call to `advance`, never a sleep.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.gate for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.quarantine import settle
from builders.swarm import GateRig
from builders.swarm.gate import INTERVAL_S

from hivemind.cell.lease_state import LeaseState
from hivemind.pheromone import TrailQuery
from hivemind.swarm import NodeStatus, SwarmNode
from hivemind.wardens import WardenState
from waggle.ids import DeviceId

_STOP_ROUNDS = 4_000  # Scheduling turns to wait for a Warden to stop, never a real sleep.


async def _quiet() -> None:
    """Let the gate's loop finish whatever it has in hand (its first pass, say)."""
    for _ in range(30):
        await asyncio.sleep(0)


async def _alarm_details(rig: GateRig, device_id: DeviceId) -> list[str]:
    """The detail of every Alarm the device's Warden has raised, oldest first."""
    served = rig.wardens.get(device_id)
    assert served is not None
    raised = await served.warden._deps.trail.query(TrailQuery(kind="alarm.raised"))
    return [str(event.payload["detail"]) for event in raised]


async def _online(rig: GateRig) -> SwarmNode:
    """Enrol a device, connect it, and announce it through the hub as the listener does."""
    node = await rig.enrol()
    device = await rig.connect(node)
    await rig.hive.hub.announce_online(node, device.link)
    return node


async def test_start_opens_the_listener_and_listens_to_the_hub(rig: GateRig) -> None:
    await rig.gate.start(rig.queen, rig.queen_deps)

    assert rig.listener.uri.startswith("ws://127.0.0.1:")
    assert rig.gate in rig.hive.hub._listeners


async def test_a_device_that_comes_online_is_given_a_warden_at_once(rig: GateRig) -> None:
    async with rig.running():
        await _quiet()

        node = await _online(rig)

        await settle(lambda: rig.wardens.get(node.device_id) is not None)
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]
        served = rig.wardens.get(node.device_id)
        assert served is not None and served.warden.state is WardenState.ACTIVE


async def test_a_device_already_connected_when_the_gate_runs_is_served_by_its_first_pass(
    rig: GateRig,
) -> None:
    node = await rig.enrol()
    await rig.connect(
        node
    )  # Dialled in between `start` and `run`, or before a restart's first pass.

    async with rig.running():
        await settle(lambda: rig.wardens.get(node.device_id) is not None)


async def test_a_device_that_goes_offline_has_its_warden_told_once_with_the_hubs_reason(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        node = await _online(rig)
        await settle(lambda: rig.wardens.get(node.device_id) is not None)
        await rig.disconnect(node)
        silent = await rig.hive.registry.set_status(
            node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
        )

        await rig.hive.hub.announce_offline(silent, "closed")
        await settle(lambda: _has_alarmed(rig, node))
        await rig.hive.hub.announce_offline(silent, "closed")  # The same loss, announced again.
        await _quiet()

        [detail] = await _alarm_details(rig, node.device_id)
        assert "closed" in detail


def _has_alarmed(rig: GateRig, node: SwarmNode) -> bool:
    """Whether the device's Warden has recorded a CELL_UNREACHABLE yet (read from its state)."""
    served = rig.wardens.get(node.device_id)
    return served is not None and served.warden._cell_lost


async def test_a_pass_runs_every_interval_even_when_the_hub_says_nothing(rig: GateRig) -> None:
    async with rig.running():
        await _quiet()  # The first pass is done, and found nothing.
        node = await rig.enrol()
        await rig.connect(node)  # No announcement: a notice that never came.
        assert rig.wardens.get(node.device_id) is None

        rig.clock.advance(INTERVAL_S)

        await settle(lambda: rig.wardens.get(node.device_id) is not None)


async def test_a_device_revoked_while_the_hive_runs_is_cut_off_within_one_interval(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        node = await _online(rig)
        await settle(lambda: rig.wardens.get(node.device_id) is not None)
        served = rig.wardens.get(node.device_id)
        assert served is not None
        event = rig.hive.recorder.swarm_event("swarm.revoked", node.device_id, {})
        await rig.hive.registry.set_status(
            node.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.clock.now(), event
        )

        await _quiet()  # The loop is waiting out its interval again, so the advance reaches it.

        rig.clock.advance(INTERVAL_S)  # By another process: only the interval can find it.

        # A Warden's stop is many awaits (its sub-bees, its lease, its trail), and the Queen
        # forgets it only once they are done: a long settle on that, the last of them.
        await settle(lambda: rig.queen.wardens == (), rounds=_STOP_ROUNDS)
        # The Warden goes first, releasing its lease over the link, and only then is it closed.
        await settle(lambda: node.device_id in rig.listener.disconnected, rounds=_STOP_ROUNDS)
        assert rig.wardens.get(node.device_id) is None
        assert served.warden.state is WardenState.STOPPED
        assert rig.queen.wardens == ()


async def test_stop_stops_every_warden_before_the_listener_closes(rig: GateRig) -> None:
    seen: list[WardenState] = []
    async with rig.running():
        await _quiet()
        nodes = [await _online(rig), await _online(rig)]
        await settle(lambda: len(rig.wardens.device_ids()) == 2)
        wardens = [rig.wardens.get(node.device_id).warden for node in nodes]  # type: ignore[union-attr]
        leases = [warden.lease for warden in wardens]
        assert all(lease is not None for lease in leases)
        rig.listener.on_stop = lambda: seen.extend(warden.state for warden in wardens)

    # Every Warden was STOPPED, its lease released while its link was up, by the time the
    # listener was told to close.
    assert seen == [WardenState.STOPPED, WardenState.STOPPED]
    assert [lease.state for lease in leases if lease is not None] == [LeaseState.RELEASED] * 2
    assert rig.wardens.device_ids() == ()


async def test_stop_is_safe_twice_and_before_run(rig: GateRig) -> None:
    await rig.gate.start(rig.queen, rig.queen_deps)

    await rig.gate.stop()
    await rig.gate.stop()

    await asyncio.wait_for(rig.gate.run(), timeout=5.0)  # Returns at once: it is already over.
    assert rig.wardens.device_ids() == ()


async def test_a_device_that_connects_after_stop_is_given_no_warden(rig: GateRig) -> None:
    async with rig.running():
        await _quiet()
    node = await rig.enrol()
    await rig.connect(node)

    await rig.hive.hub.announce_online(node, rig.devices[node.device_id].link)
    await _quiet()

    assert rig.wardens.get(node.device_id) is None  # The gate unsubscribed; nothing serves it.
    assert rig.factory.built == []
