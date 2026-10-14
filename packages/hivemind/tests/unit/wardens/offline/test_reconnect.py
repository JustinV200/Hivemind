"""Tests for hivemind.wardens.offline.reconnect: the dial with backoff, the rejoin, the record.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/reconnect.py (codingrules section 3). `redial` waits 0.5 s
    and doubles to 30 s between failed attempts, closes a connection that fails while announcing,
    closes the one in flight when it is cancelled, and stops, closing any connection it had just
    made, once it is told the link was closed. `rejoin` replays the queue, tells the
    after-reconnect step, then sends what was queued meanwhile and the held questions, and flips
    the link online last, with nothing left behind. `TrailReconnect` records `warden.reconnected`
    and ships the trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.reconnect for the module under test.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the sequence.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from builders.offline import (
    AutoClock,
    RecordingAfter,
    RecordingAnnouncer,
    ScriptedQueen,
    make_wire,
)
from builders.quarantine import settle

from hivemind.memory import MemoryIdentity
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.offline import OfflineOutbox, Reconnection, TrailReconnect, WardenRecord
from hivemind.wardens.offline import open_outbox as open_queue
from hivemind.wardens.offline import reconnect as reconnect_module
from hivemind.wardens.offline.reconnect import redial, rejoin
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError
from waggle.ids import WardenId, new_hive_id, new_node_id
from waggle.transport.base import Transport
from waggle.transport.memory import MemoryTransport

_SCHEDULE = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0]  # The schedule of ADR-0004, capped at 30 s.


class _Rig:
    """A scripted Queen, an announcer, an after step and a clock, wired as a `Reconnection`."""

    def __init__(self) -> None:
        self.clock = AutoClock()
        self.wire = make_wire(self.clock)
        self.queen = ScriptedQueen()
        self.announcer = RecordingAnnouncer(self.wire)
        self.after = RecordingAfter()
        self.parts = Reconnection(self.queen, self.announcer, self.after, self.clock)


async def test_a_failed_dial_waits_half_a_second_and_doubles_up_to_thirty() -> None:
    rig = _Rig()
    rig.queen.fail_dials = 9

    transport = await redial(rig.parts)

    assert rig.clock.sleeps == [*_SCHEDULE, 30.0, 30.0]
    assert rig.queen.dials == 10
    assert transport is not None and transport.is_connected
    await rig.queen.aclose()


async def test_the_cell_is_announced_once_on_the_connection_that_worked() -> None:
    rig = _Rig()
    rig.queen.fail_dials = 2

    await redial(rig.parts)
    await asyncio.sleep(0)

    assert rig.announcer.calls == 1
    assert rig.queen.kinds == ["cell.heartbeat"]
    await rig.queen.aclose()


async def test_a_connection_that_fails_while_announcing_is_closed_and_dialled_again() -> None:
    rig = _Rig()

    class _FlakyAnnouncer:
        calls = 0

        async def announce(self, transport: Transport) -> None:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionLostError("The link died while announcing.")
            await transport.send(rig.wire.cell_heartbeat())

    parts = Reconnection(rig.queen, _FlakyAnnouncer(), rig.after, rig.clock)

    await redial(parts)

    assert rig.queen.dials == 2
    assert rig.clock.sleeps == [0.5]
    # The first connection was closed behind it: its Queen end reads the close and ends.
    first = rig.queen.ends[0]
    assert [frame async for frame in first.receive()] == []
    await rig.queen.aclose()


async def test_a_dial_that_never_answers_is_given_up_after_its_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = _Rig()
    monkeypatch.setattr(reconnect_module, "DIAL_TIMEOUT_S", 0.05)

    class _SilentThenFine:
        calls = 0

        async def dial(self) -> Transport:
            self.calls += 1
            if self.calls == 1:
                await asyncio.Event().wait()  # The network swallows the SYN.
            return await rig.queen.dial()

    parts = Reconnection(_SilentThenFine(), rig.announcer, rig.after, rig.clock)

    transport = await redial(parts)

    assert transport is not None and transport.is_connected
    assert rig.clock.sleeps == [0.5]
    await rig.queen.aclose()


async def test_cancelling_the_redial_closes_the_connection_it_had_made() -> None:
    rig = _Rig()
    stuck = asyncio.Event()

    class _HangingAnnouncer:
        async def announce(self, transport: Transport) -> None:
            await stuck.wait()

    task = asyncio.ensure_future(
        redial(Reconnection(rig.queen, _HangingAnnouncer(), rig.after, rig.clock))
    )
    await settle(lambda: bool(rig.queen.ends))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [frame async for frame in rig.queen.ends[0].receive()] == []  # Closed behind it.
    await rig.queen.aclose()


async def test_a_redial_told_the_link_was_closed_stops_before_it_dials_again() -> None:
    rig = _Rig()
    rig.queen.fail_dials = 9

    transport = await redial(rig.parts, abandoned=lambda: rig.queen.dials >= 2)

    assert transport is None
    assert rig.queen.dials == 2  # The third attempt was never made, though the Queen still refuses.
    assert rig.clock.sleeps == [0.5, 1.0]
    await rig.queen.aclose()


async def test_a_connection_made_after_the_link_was_closed_is_closed_not_handed_on() -> None:
    rig = _Rig()
    asked = iter([False, True])  # Open as the attempt began, closed by the time it connected.

    transport = await redial(rig.parts, abandoned=lambda: next(asked))

    assert transport is None
    assert rig.queen.dials == 1
    assert not rig.queen.warden_ends[0].is_connected
    await rig.queen.aclose()


def _queue(tmp_path: Path, rig: _Rig) -> OfflineOutbox:
    return open_queue(tmp_path, Codec(), rig.clock)


async def _rejoin(rig: _Rig, queue: OfflineOutbox, order: list[str]) -> Transport:
    """Dial, then `rejoin` over the connection; `order` records the steps as they happen."""
    transport = await rig.queen.dial()

    class _Recording(RecordingAfter):
        async def reconnected(self, outage_s: float) -> None:
            order.append("after")
            await super().reconnected(outage_s)

    parts = Reconnection(rig.queen, rig.announcer, _Recording(), rig.clock)
    await rejoin(parts, queue, transport, lambda: order.append("online"), 12.5)
    return transport


async def test_the_queue_is_replayed_before_the_link_is_declared_online(tmp_path: Path) -> None:
    rig = _Rig()
    queue, wire = _queue(tmp_path, rig), rig.wire
    first, second, question = wire.result(), wire.alarm(), wire.question()
    async with queue.lock:
        await queue.queue(first)
        await queue.queue(second)
        queue.hold(question)
    order: list[str] = []

    await _rejoin(rig, queue, order)
    await asyncio.sleep(0)

    assert [e.id for e in rig.queen.received] == [first.id, second.id, question.id]
    assert order == ["after", "online"]  # Online only after the backlog and the question left.
    assert queue.queued() == 0 and queue.held() == 0
    await rig.queen.aclose()


async def test_a_message_queued_during_the_rejoin_is_sent_before_the_link_goes_online(
    tmp_path: Path,
) -> None:
    rig = _Rig()
    queue, wire = _queue(tmp_path, rig), rig.wire
    early, late = wire.result(), wire.alarm()
    async with queue.lock:
        await queue.queue(early)
    order: list[str] = []
    transport = await rig.queen.dial()

    class _QueuesMeanwhile:
        async def reconnected(self, outage_s: float) -> None:
            # The Warden sends something while the trail is being shipped: it is queued.
            async with queue.lock:
                await queue.queue(late)
            order.append("after")

    parts = Reconnection(rig.queen, rig.announcer, _QueuesMeanwhile(), rig.clock)

    await rejoin(parts, queue, transport, lambda: order.append("online"), 1.0)
    await asyncio.sleep(0)

    assert [e.id for e in rig.queen.received] == [early.id, late.id]  # Nothing left behind.
    assert order == ["after", "online"]
    await rig.queen.aclose()


async def test_the_after_step_is_told_how_long_the_link_was_down(tmp_path: Path) -> None:
    rig = _Rig()
    queue = _queue(tmp_path, rig)
    transport = await rig.queen.dial()

    await rejoin(rig.parts, queue, transport, lambda: None, 42.5)

    assert rig.after.outages == [42.5]
    await rig.queen.aclose()


async def test_a_link_that_dies_mid_replay_leaves_every_unsent_message_queued(
    tmp_path: Path,
) -> None:
    rig = _Rig()
    queue, wire = _queue(tmp_path, rig), rig.wire
    async with queue.lock:
        for build in (wire.result, wire.alarm, wire.progress):
            await queue.queue(build())
    mine, _theirs = MemoryTransport.pair(Codec(), Codec())
    online: list[str] = []

    class _CableOut:
        """Takes one frame, then behaves as a link that has just been pulled."""

        sent = 0

        async def send(self, envelope: Envelope) -> None:
            if self.sent == 1:
                raise ConnectionLostError("The cable was pulled.")
            self.sent += 1
            await mine.send(envelope)

    with pytest.raises(ConnectionLostError):
        await rejoin(
            rig.parts, queue, cast(Transport, _CableOut()), lambda: online.append("online"), 1.0
        )

    assert queue.queued() == 2  # The first was sent and acknowledged; the others wait.
    assert online == []  # The link never reported online.


async def test_trail_reconnect_records_the_reconnection_and_ships_the_trail() -> None:
    clock = AutoClock()
    wire = make_wire(clock)
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    shipped: list[int] = []
    steps: list[str] = []

    class _Sync:
        async def sync(self) -> bool:
            steps.append("sync")
            # The reconnection must already be on the trail by the time it ships.
            shipped.append(len(await trail.query(TrailQuery())))
            return True

        def rewind(self, window: timedelta) -> None:
            steps.append(f"rewind {window.total_seconds():.0f}")

    step = TrailReconnect(WardenRecord(trail, identity, WardenId(wire.hop.sender), clock), _Sync())

    await step.reconnected(12.3456)

    (event,) = await trail.query(TrailQuery())
    assert event.kind == "warden.reconnected"
    assert event.subject_id == WardenId(wire.hop.sender)
    assert event.payload == {"outage_s": 12.346}
    assert shipped == [1]
    # What went out while the link was dead and nobody knew goes out again: twice what a websocket's
    # keepalive takes to give up on a silent peer (20 s to ping, 20 s to wait for the pong).
    assert steps == ["rewind 80", "sync"]


async def test_trail_reconnect_without_a_sync_only_records() -> None:
    clock = AutoClock()
    wire = make_wire(clock)
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )

    await TrailReconnect(
        WardenRecord(trail, identity, WardenId(wire.hop.sender), clock), None
    ).reconnected(1.0)

    assert [e.kind for e in await trail.query(TrailQuery())] == ["warden.reconnected"]
