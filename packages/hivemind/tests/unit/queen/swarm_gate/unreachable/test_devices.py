"""Tests for hivemind.queen.swarm_gate.unreachable.devices: beginning, keeping and ending a hold.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/unreachable/devices.py (codingrules section 3); the
    decision at the end of the grace is in test_devices_decisions.py. The Warden is the real
    class, so the Alarm it raises goes through the ordinary Alarm path onto its own trail; time is
    the rig's FakeClock.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.unreachable.devices for the module under test.
    - hivemind.wardens.ticks.lease for the Alarm and the lease treated as lost.
"""

from __future__ import annotations

from builders.swarm import GRACE_S, GateRig, place_on_device

from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.swarm_gate import HoldState, UnreachableDevices
from hivemind.queen.swarm_gate.unreachable import NO_LINK_REASON, WARDEN_LINK_REASON
from hivemind.swarm import NodeStatus, RuntimeLevel, SwarmNode
from waggle.ids import DeviceId


class _RecordingDiagnosis:
    """A diagnosis that notes each device it is asked to begin and to end, and why."""

    def __init__(self) -> None:
        self.begun: list[tuple[DeviceId, str]] = []
        self.ended: list[DeviceId] = []

    async def begin(self, node: SwarmNode, reason: str) -> None:
        self.begun.append((node.device_id, reason))

    async def end(self, node: SwarmNode) -> None:
        self.ended.append(node.device_id)


async def _alarms(rig: GateRig, device_id: DeviceId) -> list[PheromoneEvent]:
    """Every `alarm.raised` on the trail of `device_id`'s Warden, oldest first."""
    served = rig.wardens.get(device_id)
    assert served is not None
    return list(await served.warden._deps.trail.query(TrailQuery(kind="alarm.raised")))


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


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

        await rig.unreachable.found(node)
        rig.unreachable.note(node.device_id, "closed")
        await rig.unreachable.lost(node)

        alarms = await _alarms(rig, node.device_id)
        assert len(alarms) == 2
        assert "closed" in str(alarms[1].payload["detail"])


async def test_found_forgets_the_reason_the_device_went(rig: GateRig) -> None:
    async with rig.serving():
        node = await _served(rig)
        rig.unreachable.note(node.device_id, "closed")

        await rig.unreachable.found(node)
        await rig.unreachable.lost(node)

        [alarm] = await _alarms(rig, node.device_id)
        assert "closed" not in str(alarm.payload["detail"])  # A stale reason is not the new one.


async def test_lost_and_found_do_nothing_for_a_device_with_no_warden(rig: GateRig) -> None:
    node = await rig.enrol()
    handler = UnreachableDevices(rig.wardens, rig.hive.registry, rig.hive.recorder, rig.terms)

    await handler.lost(node)  # Must not raise, bound to no Queen yet.
    await handler.found(node)

    assert rig.wardens.get(node.device_id) is None
    assert handler.hold_of(node.device_id) is None


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


async def test_a_lost_device_has_its_cell_held_and_the_hold_recorded_once(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        rig.unreachable.note(placed.node.device_id, "closed")

        await rig.unreachable.lost(placed.node)
        await rig.unreachable.lost(placed.node)  # The next pass finds it still gone.

        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        assert rig.unreachable.hold_of(placed.node.device_id) is HoldState.HELD
        [held] = await rig.hive.events("swarm.node_held")
        assert held.subject_id == placed.node.device_id
        assert held.payload == {
            "device_id": placed.node.device_id,
            "node_id": placed.node.node_id,
            "cell_id": placed.node.cell_id,
            "reason": "closed",
            "level": "GATEWAY",
            "grace_s": GRACE_S,
            "tasks": 1,
        }


async def test_nothing_is_failed_or_taken_back_while_the_grace_runs(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        await rig.unreachable.lost(placed.node)

        rig.clock.advance(GRACE_S - 1.0)
        await rig.unreachable.lost(placed.node)

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.warden_id == placed.node.warden_id  # Still placed on its Warden.
        assert task.status.value == "RUNNING"
        assert await _decisions(rig) == []
        assert rig.wardens.get(placed.node.device_id) is not None


async def test_a_colonized_device_whose_warden_is_lost_is_marked_unreachable(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        # Its heartbeats go on (the packet is up), so nothing else would call it unreachable.
        assert placed.node.status is NodeStatus.ONLINE

        await rig.unreachable.warden_lost(placed.node)

        row = await rig.hive.registry.node(placed.node.device_id)
        assert row is not None and row.status is NodeStatus.UNREACHABLE
        [unreachable] = await rig.hive.events("swarm.unreachable")
        assert unreachable.payload["reason"] == WARDEN_LINK_REASON
        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["level"] == "COLONIZED"


async def test_a_level0_device_is_left_for_the_monitor_to_mark_unreachable(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        await rig.unreachable.lost(placed.node)

        assert await rig.hive.events("swarm.unreachable") == []  # The heartbeat monitor's to write.
        row = await rig.hive.registry.node(placed.node.device_id)
        assert row is not None and row.status is NodeStatus.ONLINE


async def test_a_device_found_inside_the_grace_has_its_hold_cancelled(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)
        await rig.unreachable.lost(placed.node)
        rig.clock.advance(GRACE_S / 2)

        await rig.unreachable.found(placed.node)

        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        assert rig.unreachable.hold_of(placed.node.device_id) is None
        assert await _decisions(rig) == [("hold_released", "reconnected")]
        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.warden_id == placed.node.warden_id  # Nothing was lost.
        rig.clock.advance(GRACE_S)
        await rig.unreachable.found(placed.node)  # A second report changes nothing.
        assert await _decisions(rig) == [("hold_released", "reconnected")]


async def test_found_for_a_device_with_no_hold_records_nothing(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig)

        await rig.unreachable.found(placed.node)

        assert await _decisions(rig) == []


async def test_the_diagnosis_is_begun_with_the_reason_and_ended_when_the_device_returns(
    rig: GateRig,
) -> None:
    diagnosis = _RecordingDiagnosis()
    rig.unreachable.use_diagnosis(diagnosis)
    async with rig.serving():
        placed = await place_on_device(rig)
        rig.unreachable.note(placed.node.device_id, "missed_heartbeats")

        await rig.unreachable.lost(placed.node)
        await rig.unreachable.lost(placed.node)
        await rig.unreachable.found(placed.node)

        assert diagnosis.begun == [(placed.node.device_id, "missed_heartbeats")]
        assert diagnosis.ended == [placed.node.device_id]


async def test_a_hold_with_no_task_asks_for_no_diagnosis(rig: GateRig) -> None:
    # A Hive that has just started finds every enrolled device that has not dialled in yet
    # unreachable; with nothing held nobody waits on a diagnosis, so the Hive Stand gets no Drone.
    diagnosis = _RecordingDiagnosis()
    rig.unreachable.use_diagnosis(diagnosis)
    async with rig.serving():
        node = await _served(rig)

        await rig.unreachable.lost(node)
        await rig.unreachable.found(node)

        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["tasks"] == 0
        assert diagnosis.begun == []
        assert diagnosis.ended == []


async def test_a_hold_on_a_device_nobody_saw_go_asks_for_no_diagnosis_even_with_a_task(
    rig: GateRig,
) -> None:
    diagnosis = _RecordingDiagnosis()
    rig.unreachable.use_diagnosis(diagnosis)
    async with rig.serving():
        # A task is on the device, but the hub gave no reason because nothing went: the Hive
        # restarted without a notice, or the packet has not dialled yet. It is not lost, only not
        # here yet.
        placed = await place_on_device(rig)

        await rig.unreachable.lost(placed.node)
        await rig.unreachable.lost(placed.node)
        rig.clock.advance(GRACE_S + 1.0)
        await rig.unreachable.lost(placed.node)  # The grace ends with the device still not here.

        # It is held and decided on like any device that is not reachable ...
        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["reason"] == NO_LINK_REASON and held.payload["tasks"] == 1
        assert "unreachable_decision" in {reason for reason, _detail in await _decisions(rig)}
        # ... but nobody was asked to look into it, then or when the hold ended.
        assert diagnosis.begun == [] and diagnosis.ended == []


async def test_forget_ends_a_hold_with_no_decision(rig: GateRig) -> None:
    diagnosis = _RecordingDiagnosis()
    rig.unreachable.use_diagnosis(diagnosis)
    async with rig.serving():
        placed = await place_on_device(rig)
        rig.unreachable.note(placed.node.device_id, "closed")
        await rig.unreachable.lost(placed.node)

        await rig.unreachable.forget(placed.node)

        assert not rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
        assert rig.unreachable.hold_of(placed.node.device_id) is None
        assert await _decisions(rig) == []
        assert diagnosis.ended == [placed.node.device_id]


async def test_a_wardens_lost_link_notes_its_own_reason_and_holds_the_device(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)

        await rig.unreachable.warden_lost(placed.node)

        [held] = await rig.hive.events("swarm.node_held")
        assert held.payload["reason"] == WARDEN_LINK_REASON
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)
