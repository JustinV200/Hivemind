"""Tests for hivemind.queen.swarm_gate.unreachable: telling a device's Warden its link is lost.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/unreachable.py (codingrules section 3). The Warden is the
    real class, so the Alarm it raises goes through the ordinary Alarm path onto its own trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.unreachable for the module under test.
    - hivemind.wardens.ticks.lease for the Alarm and the lease treated as lost.
"""

from __future__ import annotations

from builders.swarm import GateRig

from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.swarm_gate import UnreachableDevices
from hivemind.queen.swarm_gate.unreachable import NO_LINK_REASON
from hivemind.swarm import SwarmNode
from waggle.ids import DeviceId


async def _alarms(rig: GateRig, device_id: DeviceId) -> list[PheromoneEvent]:
    """Every `alarm.raised` on the trail of `device_id`'s Warden, oldest first."""
    served = rig.wardens.get(device_id)
    assert served is not None
    return list(await served.warden._deps.trail.query(TrailQuery(kind="alarm.raised")))


async def _served(rig: GateRig) -> SwarmNode:
    """Enrol a device and give it a Warden; return its node."""
    node = await rig.enrol()
    assert await rig.wardens.serve(node)
    return node


async def test_lost_has_the_wardens_alarm_once_with_the_reason_the_hub_gave(rig: GateRig) -> None:
    async with rig.serving():
        node = await _served(rig)
        rig.unreachable.note(node.device_id, "missed_heartbeats")

        await rig.unreachable.lost(node)
        await rig.unreachable.lost(node)  # The same loss, told again by the next reconcile pass.

        [alarm] = await _alarms(rig, node.device_id)
        assert alarm.payload["kind"] == "CELL_UNREACHABLE"
        assert alarm.payload["severity"] == "WARNING"
        assert "missed_heartbeats" in str(alarm.payload["detail"])


async def test_a_loss_with_no_reason_noted_says_there_was_no_live_link(rig: GateRig) -> None:
    async with rig.serving():
        node = await _served(rig)

        await rig.unreachable.lost(node)

        [alarm] = await _alarms(rig, node.device_id)
        assert NO_LINK_REASON in str(alarm.payload["detail"])


async def test_a_device_that_is_found_and_lost_again_is_a_new_loss(rig: GateRig) -> None:
    async with rig.serving():
        node = await _served(rig)
        await rig.unreachable.lost(node)

        rig.unreachable.found(node)
        rig.unreachable.note(node.device_id, "closed")
        await rig.unreachable.lost(node)

        alarms = await _alarms(rig, node.device_id)
        assert len(alarms) == 2
        assert "closed" in str(alarms[1].payload["detail"])


async def test_found_forgets_the_reason_the_device_went(rig: GateRig) -> None:
    async with rig.serving():
        node = await _served(rig)
        rig.unreachable.note(node.device_id, "closed")

        rig.unreachable.found(node)
        await rig.unreachable.lost(node)

        [alarm] = await _alarms(rig, node.device_id)
        assert "closed" not in str(alarm.payload["detail"])  # A stale reason is not the new one.


async def test_lost_and_found_do_nothing_for_a_device_with_no_warden(rig: GateRig) -> None:
    node = await rig.enrol()
    handler = UnreachableDevices(rig.wardens)

    await handler.lost(node)  # Must not raise.
    handler.found(node)

    assert rig.wardens.get(node.device_id) is None


async def test_a_lost_device_keeps_its_warden_but_not_an_idle_lease(rig: GateRig) -> None:
    async with rig.serving():
        node = await _served(rig)
        served = rig.wardens.get(node.device_id)
        assert served is not None

        await rig.unreachable.lost(node)

        # The Warden is kept, and so is its lease: a release nothing could confirm only orphans it.
        assert rig.wardens.get(node.device_id) is served
        assert served.warden.lease is not None
        assert served.warden._cell_lost is True
