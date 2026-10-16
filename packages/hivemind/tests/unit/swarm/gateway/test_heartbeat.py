"""Tests for hivemind.swarm.gateway.heartbeat: whether each device is reachable, and the record.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/heartbeat.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). The registry is the in-memory one, the hub is real and the link is a raw
    one, so the monitor is tested against the collaborators it runs with.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.heartbeat for the module under test.
    - docs/waggle/enrolment.md sections 8 and 13 for the rules it applies.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from builders.cells import make_identity
from builders.swarm import (
    HubLog,
    RawLink,
    enrol_node,
    make_capacity_report,
    make_raw_link,
    make_swarm_node,
)

from hivemind.manifest import SwarmSection
from hivemind.pheromone import SwarmEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.errors import NodeRevokedError, SwarmError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway import (
    GatewayHub,
    HeartbeatMonitor,
    Liveness,
    LivenessLoop,
    LivenessRules,
    judge_liveness,
)
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import MemorySwarmRegistry
from waggle.ids import DeviceId, new_cell_id, new_lease_id
from waggle.messages.session.commands import SessionClose
from waggle.messages.swarm import DeviceHeartbeat


class _Racy(MemorySwarmRegistry):
    """A registry where another writer moves a node just before this writer's compare-and-set.

    With `yielding` set, every read and write first gives the event loop a turn, as a real SQLite
    registry's thread hop does, so two tasks' sequences of registry calls interleave.
    """

    racing_to: NodeStatus | None = None
    yielding: bool = False

    async def _turn(self) -> None:
        """Give another task the loop, when interleaving is wanted."""
        if self.yielding:
            await asyncio.sleep(0)

    async def node(self, device_id: DeviceId) -> SwarmNode | None:
        """Read the node, after a turn of the loop when `yielding`."""
        await self._turn()
        return await super().node(device_id)

    async def touch(
        self, device_id: DeviceId, at: datetime, runtime_version: str | None = None
    ) -> None:
        """Touch the node, after a turn of the loop when `yielding`."""
        await self._turn()
        await super().touch(device_id, at, runtime_version)

    async def set_status(
        self,
        device_id: DeviceId,
        expected: NodeStatus,
        new: NodeStatus,
        at: datetime,
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Let the rival win first, once, then apply the caller's own move."""
        await self._turn()
        if self.racing_to is not None:
            rival, self.racing_to = self.racing_to, None
            await super().set_status(device_id, expected, rival, at)
        return await super().set_status(device_id, expected, new, at, event)


@dataclass
class _Rig:
    raw: RawLink
    registry: _Racy
    hub: GatewayHub
    monitor: HeartbeatMonitor
    listener: HubLog
    trail: MemoryPheromoneTrail
    node: SwarmNode

    async def kinds(self) -> list[str]:
        """The kinds on the trail, oldest first."""
        return [event.kind for event in await self.trail.query(TrailQuery())]

    async def stored(self) -> SwarmNode:
        """The node as the registry holds it."""
        found = await self.registry.node(self.node.device_id)
        assert found is not None
        return found


@pytest.fixture
async def rig() -> AsyncIterator[_Rig]:
    """A monitor over a registry holding one enrolled device, a hub and a raw link."""
    raw = await make_raw_link()
    trail = MemoryPheromoneTrail(raw.clock)
    registry = _Racy(trail)
    node = await enrol_node(registry, make_swarm_node(raw.party, raw.clock), raw.clock)
    hub, listener = GatewayHub(raw.clock), HubLog()
    hub.subscribe(listener)
    recorder = SwarmRecorder(trail, raw.clock, make_identity(raw.clock, hive_id=raw.hive.hive_id))
    monitor = HeartbeatMonitor(registry, hub, recorder, LivenessRules(miss_limit=4))
    yield _Rig(raw, registry, hub, monitor, listener, trail, node)
    await raw.close()


async def _unreachable(rig: _Rig) -> SwarmNode:
    """Move the rig's node to UNREACHABLE in the registry, as a sweep would have."""
    return await rig.registry.set_status(
        rig.node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.raw.clock.now()
    )


@pytest.mark.parametrize(
    ("silence", "verdict"),
    [
        (0.0, Liveness.ALIVE),
        (60.0, Liveness.ALIVE),
        (60.1, Liveness.MISSED),
        (3600.0, Liveness.MISSED),
    ],
)
def test_a_device_is_missed_only_past_the_limit_at_its_own_interval(
    silence: float, verdict: Liveness
) -> None:
    assert judge_liveness(silence, 15.0, 4) is verdict


def test_the_rules_take_the_miss_limit_from_the_manifest_section() -> None:
    rules = LivenessRules.from_section(SwarmSection(heartbeat_miss_limit=7))

    assert rules.miss_limit == 7 and rules.sweep_interval_s > 0


async def test_admitting_an_online_device_touches_it_and_changes_no_status(rig: _Rig) -> None:
    rig.raw.clock.advance(10.0)

    admitted = await rig.monitor.admit(rig.node)

    assert admitted.status is NodeStatus.ONLINE
    assert (await rig.stored()).last_seen_at == rig.raw.clock.now()


async def test_a_link_on_a_device_that_was_already_online_is_still_recorded_as_swarm_online(
    rig: _Rig,
) -> None:
    # The first connection after enrolment, a reconnect the sweep had not noticed, the Hive
    # restarting under a node the registry still calls online: enrolment.md section 8 puts every
    # established link on the trail, not only the move out of UNREACHABLE.
    await rig.monitor.admit(rig.node)
    await rig.monitor.admit(rig.node)

    online = await rig.trail.query(TrailQuery(kind="swarm.online"))

    assert len(online) == 2
    assert {event.subject_id for event in online} == {rig.node.device_id}
    assert {event.payload["reason"] for event in online} == {"link_connected"}
    assert {event.payload["node_id"] for event in online} == {rig.node.node_id}
    assert (await rig.stored()).status is NodeStatus.ONLINE  # No status moved: just the record.


async def test_admitting_an_unreachable_device_brings_it_online_and_records_why(rig: _Rig) -> None:
    await _unreachable(rig)

    admitted = await rig.monitor.admit(rig.node)

    assert admitted.status is NodeStatus.ONLINE
    assert await rig.kinds() == ["swarm.online"]
    online = (await rig.trail.query(TrailQuery(kind="swarm.online")))[0]
    assert online.payload["reason"] == "link_connected"


async def test_admitting_a_revoked_device_is_refused(rig: _Rig) -> None:
    await rig.registry.set_status(
        rig.node.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.raw.clock.now()
    )

    with pytest.raises(NodeRevokedError):
        await rig.monitor.admit(rig.node)


async def test_admission_that_loses_the_race_to_another_writer_reads_what_won(rig: _Rig) -> None:
    await _unreachable(rig)
    rig.registry.racing_to = NodeStatus.ONLINE  # Someone else restored it first.

    admitted = await rig.monitor.admit(rig.node)

    assert admitted.status is NodeStatus.ONLINE
    assert await rig.kinds() == []  # The record of the move is the other writer's.


async def test_a_link_that_ends_marks_the_device_unreachable_and_tells_the_listeners(
    rig: _Rig,
) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)

    await rig.monitor.link_down(rig.node, rig.raw.link, "closed")

    assert (await rig.stored()).status is NodeStatus.UNREACHABLE
    assert rig.hub.link(rig.node.device_id) is None
    assert [reason for _node, reason in rig.listener.offline] == ["closed"]
    assert await rig.kinds() == ["swarm.unreachable"]


async def test_an_old_link_ending_after_a_reconnect_takes_nothing_offline(rig: _Rig) -> None:
    second = await make_raw_link()
    try:
        await rig.hub.attach(rig.node, rig.raw.link)
        await rig.hub.attach(rig.node, second.link)

        await rig.monitor.link_down(rig.node, rig.raw.link, "closed")

        assert (await rig.stored()).status is NodeStatus.ONLINE
        assert rig.listener.offline == []
        assert rig.hub.link(rig.node.device_id) is second.link
    finally:
        await second.close()


async def test_an_old_link_dying_while_its_replacement_is_admitted_leaves_the_device_online(
    rig: _Rig,
) -> None:
    # The router admits a new connection before it attaches the link, so the old link's own end
    # (a keepalive timeout) can detach and move ONLINE -> UNREACHABLE in another task while
    # `admit` is between reading the status and acting on it. Interleaved call by call, the old
    # link's move must be read by the admission, not lost under its stale read.
    await rig.hub.attach(rig.node, rig.raw.link)
    rig.registry.yielding = True

    _down, admitted = await asyncio.gather(
        rig.monitor.link_down(rig.node, rig.raw.link, "closed"),
        rig.monitor.admit(rig.node),
    )

    assert admitted.status is NodeStatus.ONLINE
    assert (await rig.stored()).status is NodeStatus.ONLINE
    assert await rig.kinds() == ["swarm.unreachable", "swarm.online"]
    assert [reason for _node, reason in rig.listener.offline] == ["closed"]


async def test_a_link_ending_on_an_already_unreachable_device_still_announces_it(
    rig: _Rig,
) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    await _unreachable(rig)

    await rig.monitor.link_down(rig.node, rig.raw.link, "closed")

    assert len(rig.listener.offline) == 1
    assert await rig.kinds() == []  # The sweep already moved it; no second record.


async def test_a_heartbeat_touches_the_device_and_is_passed_on(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    rig.raw.clock.advance(20.0)
    beat = await rig.raw.send_heartbeat()

    await rig.monitor.frame(rig.node, beat)

    assert (await rig.stored()).last_seen_at == rig.raw.clock.now()
    assert rig.listener.frames == [beat]


async def test_a_heartbeat_restores_an_unreachable_device_and_announces_it_once(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    await _unreachable(rig)

    await rig.monitor.frame(rig.node, await rig.raw.send_heartbeat())
    await rig.monitor.frame(rig.node, await rig.raw.send_heartbeat())

    assert (await rig.stored()).status is NodeStatus.ONLINE
    assert len(rig.listener.online) == 1
    online = (await rig.trail.query(TrailQuery(kind="swarm.online")))[0]
    assert online.payload["reason"] == "heartbeat_resumed"


async def test_a_restoring_heartbeat_that_loses_the_race_announces_nothing(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    await _unreachable(rig)
    rig.registry.racing_to = NodeStatus.ONLINE

    await rig.monitor.frame(rig.node, await rig.raw.send_heartbeat())

    assert rig.listener.online == []


async def test_a_heartbeat_naming_another_device_is_not_this_devices_word(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    await _unreachable(rig)
    other = make_swarm_node()
    foreign = await rig.raw.send(
        DeviceHeartbeat(
            device_id=other.device_id,
            level=other.level,
            lease_ids=(),
            hive_started_processes=0,
            outbox_pending=0,
            uptime_s=1.0,
        )
    )

    await rig.monitor.frame(rig.node, foreign)

    assert (await rig.stored()).status is NodeStatus.UNREACHABLE


async def test_a_heartbeat_for_a_revoked_device_changes_nothing(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    await rig.registry.set_status(
        rig.node.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.raw.clock.now()
    )

    await rig.monitor.frame(rig.node, await rig.raw.send_heartbeat())

    assert (await rig.stored()).status is NodeStatus.REVOKED


async def test_a_capacity_report_updates_the_stored_capacity_and_the_hubs_record(
    rig: _Rig,
) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    report = await rig.raw.send(make_capacity_report(rig.raw.party))

    await rig.monitor.frame(rig.node, report)

    stored = await rig.stored()
    assert (stored.capacity.host.cores, stored.capacity.max_sub_bees) == (16, 2)
    assert rig.hub.node(rig.node.device_id) == stored
    assert rig.listener.frames == [report]


async def test_a_capacity_report_about_another_cell_is_ignored(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    foreign = await rig.raw.send(
        make_capacity_report(rig.raw.party, cores=64, cell_id=new_cell_id(rig.raw.clock))
    )

    await rig.monitor.frame(rig.node, foreign)

    assert (await rig.stored()).capacity.host.cores == rig.node.capacity.host.cores


async def test_a_capacity_report_for_a_revoked_device_is_ignored(rig: _Rig) -> None:
    await rig.registry.set_status(
        rig.node.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.raw.clock.now()
    )

    await rig.monitor.frame(
        rig.node, await rig.raw.send(make_capacity_report(rig.raw.party, cores=64))
    )

    assert (await rig.stored()).capacity.host.cores == rig.node.capacity.host.cores


async def test_a_frame_with_no_handler_is_only_passed_on(rig: _Rig) -> None:
    event = await rig.raw.send(SessionClose(lease_id=new_lease_id(rig.raw.clock), reason="x"))

    await rig.monitor.frame(rig.node, event)

    assert rig.listener.frames == [event]


async def test_a_sweep_marks_a_silent_linked_device_unreachable_and_announces_it(
    rig: _Rig,
) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    rig.raw.clock.advance(61.0)  # Past four missed beats of 15 s.

    marked = await rig.monitor.sweep()

    assert marked == (rig.node.device_id,)
    assert (await rig.stored()).status is NodeStatus.UNREACHABLE
    assert [reason for _node, reason in rig.listener.offline] == ["missed_heartbeats"]


async def test_a_sweep_leaves_a_device_that_spoke_recently_alone(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    rig.raw.clock.advance(59.0)

    assert await rig.monitor.sweep() == ()
    assert (await rig.stored()).status is NodeStatus.ONLINE


async def test_a_sweep_judges_a_device_with_no_link_by_when_it_was_last_seen(rig: _Rig) -> None:
    rig.raw.clock.advance(30.0)
    assert await rig.monitor.sweep() == ()

    rig.raw.clock.advance(31.0)
    marked = await rig.monitor.sweep()

    assert marked == (rig.node.device_id,)
    assert rig.listener.offline == []  # Nobody was connected to tell.


async def test_a_sweep_that_loses_the_race_marks_nothing(rig: _Rig) -> None:
    rig.raw.clock.advance(120.0)
    rig.registry.racing_to = NodeStatus.REVOKED

    assert await rig.monitor.sweep() == ()
    assert (await rig.stored()).status is NodeStatus.REVOKED


async def test_the_loop_sweeps_each_interval_and_stops(rig: _Rig) -> None:
    await rig.hub.attach(rig.node, rig.raw.link)
    loop = LivenessLoop(rig.monitor, rig.raw.clock, 5.0)
    task = asyncio.ensure_future(loop.run())
    await asyncio.sleep(0)

    rig.raw.clock.advance(70.0)
    await asyncio.sleep(0.05)
    loop.stop()
    rig.raw.clock.advance(5.0)
    await asyncio.wait_for(task, 2.0)

    assert (await rig.stored()).status is NodeStatus.UNREACHABLE


async def test_a_sweep_that_fails_is_logged_and_the_loop_carries_on(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def failing_once() -> tuple[DeviceId, ...]:
        calls.append(1)
        if len(calls) == 1:
            raise SwarmError("the registry hiccupped")
        return ()

    monkeypatch.setattr(rig.monitor, "sweep", failing_once)
    loop = LivenessLoop(rig.monitor, rig.raw.clock, 5.0)
    task = asyncio.ensure_future(loop.run())
    await asyncio.sleep(0)

    for _ in range(3):
        rig.raw.clock.advance(35.0)
        await asyncio.sleep(0.02)
    loop.stop()
    rig.raw.clock.advance(35.0)
    await asyncio.wait_for(task, 2.0)

    assert len(calls) >= 2
