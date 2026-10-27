"""Tests for hivemind.wardens.offline.link: a Transport to the Queen that mends itself.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/link.py (codingrules section 3). Over a scripted Queen on
    in-memory connections: the link announces the Cell on every connection, forwards sends while
    up, routes them by the offline table while down, reports its phase to a watcher, never ends its
    `receive` before it is closed (so a Warden reading it does not spin), dials no more once it is
    closed, and catches the Queen up in order after a drop. The same behaviour over a real socket
    is `test_link_socket.py`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.link for the module under test.
    - builders.offline for the scripted Queen and the rig that drives the link.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.offline import AutoClock, LinkRig, ScriptedQueen

from hivemind.wardens.offline import LinkPhase, OfflineRefusedError
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.errors import TransportClosedError
from waggle.messages.task import TaskStage


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[LinkRig]:
    """A link over a scripted Queen, already pumping; closed and reaped after the test."""
    started = LinkRig(tmp_path)
    started.start()
    yield started
    await started.stop()


async def _online(rig: LinkRig) -> None:
    """Wait for the first connection to be up and announced."""
    await rig.until(lambda: rig.link.is_online and "cell.heartbeat" in rig.queen.kinds)


async def _cut(rig: LinkRig) -> None:
    """Pull the cable and keep the Queen out of reach until the test lets it back."""
    rig.queen.block_dials()
    rig.queen.drop()
    await rig.until(lambda: not rig.link.is_online)


async def test_the_first_connection_announces_the_cell_and_goes_online(rig: LinkRig) -> None:
    await _online(rig)

    assert rig.link.is_connected and rig.link.phase is LinkPhase.ONLINE
    assert rig.phases == [LinkPhase.RECOVERING, LinkPhase.ONLINE]
    assert rig.queen.kinds == ["cell.heartbeat"]
    assert rig.after.outages == [0.0]


async def test_while_online_every_send_goes_straight_to_the_queen(rig: LinkRig) -> None:
    await _online(rig)
    wire = rig.wire

    for envelope in (wire.result(), wire.heartbeat(), wire.cell_request()):
        await rig.link.send(envelope)  # Nothing is queued, dropped or refused while up.
    await rig.until(lambda: len(rig.queen.received) == 4)

    assert rig.queen.kinds == [
        "cell.heartbeat",
        "task.result",
        "supervision.heartbeat",
        "cell.request",
    ]
    assert rig.outbox.queued() == 0


async def test_a_dropped_link_reports_offline_then_recovers_and_announces_again(
    rig: LinkRig,
) -> None:
    await _online(rig)
    rig.phases.clear()

    rig.queen.drop()
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 2)

    assert rig.phases == [LinkPhase.OFFLINE, LinkPhase.RECOVERING, LinkPhase.ONLINE]
    assert rig.queen.kinds.count("cell.heartbeat") == 2  # The Queen's copy of the Cell is renewed.
    assert len(rig.after.outages) == 2


async def test_while_down_each_message_takes_the_road_the_table_gives_it(rig: LinkRig) -> None:
    await _online(rig)
    wire, link = rig.wire, rig.link
    await _cut(rig)

    result, alarm, checkpoint = wire.result(), wire.alarm(), wire.progress()
    request, question = wire.forage_request(), wire.question()
    for envelope in (result, alarm, checkpoint, request, question):
        await link.send(envelope)  # Queued, or held: none of them raises.
    await link.send(wire.heartbeat())  # Dropped.
    await link.send(wire.progress(TaskStage.WORKING))  # Dropped: only a status.
    with pytest.raises(OfflineRefusedError):
        await link.send(wire.cell_request())  # Refused: an offline Warden cannot get a Cell.

    assert rig.outbox.queued() == 4 and rig.outbox.held() == 1
    rig.queen.release_dials()
    await rig.until(lambda: len(rig.queen.received) == 2 + 5)  # Two announcements, five messages.

    replayed = rig.queen.received[2:]
    # The events keep their ids and their order; the request is fresh; the question goes last.
    assert [e.kind for e in replayed] == [
        "task.result",
        "supervision.alarm_raised",
        "task.progress",
        "forage.request",
        "supervision.question",
    ]
    assert [e.id for e in replayed[:3]] == [result.id, alarm.id, checkpoint.id]
    assert replayed[3].id != request.id and replayed[3].payload == request.payload
    assert replayed[4].id == question.id
    assert "supervision.heartbeat" not in rig.queen.kinds and "cell.request" not in rig.queen.kinds


async def test_a_send_that_finds_the_link_dead_is_queued_not_lost(rig: LinkRig) -> None:
    await _online(rig)
    rig.queen.block_dials()
    rig.queen.drop()  # The link has not noticed yet: no await has run since the cable was pulled.
    result = rig.wire.result()

    await rig.link.send(result)  # Tried live, found dead, queued.

    assert not rig.link.is_online and rig.outbox.queued() == 1
    rig.queen.release_dials()
    await rig.until(lambda: result.id in [e.id for e in rig.queen.received])


async def test_receive_waits_while_down_so_a_warden_reading_it_does_not_spin(rig: LinkRig) -> None:
    await _online(rig)
    await _cut(rig)

    for _ in range(50):
        await asyncio.sleep(0)

    assert rig.pump_is_running  # It never ended: the bare client's iterator would have.
    assert rig.queen.dials == 2  # One dial in flight, not a loop of them.
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online)
    await rig.queen.send(rig.wire.result())  # Frames flow again over the new connection.
    await rig.until(lambda: len(rig.heard) == 1)


async def test_frames_from_the_queen_arrive_in_order_across_a_reconnection(rig: LinkRig) -> None:
    await _online(rig)
    first, second = rig.wire.result(), rig.wire.alarm()
    await rig.queen.send(first)
    await rig.until(lambda: len(rig.heard) == 1)

    rig.queen.drop()
    await rig.until(lambda: rig.queen.dials == 2 and rig.link.is_online)
    await rig.queen.send(second)
    await rig.until(lambda: len(rig.heard) == 2)

    assert [e.id for e in rig.heard] == [first.id, second.id]


async def test_a_queen_that_closes_cleanly_is_a_lost_link_too(rig: LinkRig) -> None:
    await _online(rig)

    await rig.queen.close_current()  # A restarting Queen closes with 1000, not a drop.
    await rig.until(lambda: rig.queen.dials == 2 and rig.link.is_online)

    assert rig.pump_is_running


async def test_a_readable_frame_with_a_bad_payload_is_skipped_on_the_same_connection(
    rig: LinkRig,
) -> None:
    await _online(rig)
    frame = json.loads(Codec().encode(rig.wire.result()))
    frame["payload"] = {"summary": 1}  # The id is readable; the payload does not validate.
    good = rig.wire.alarm()

    rig.queen.warden_ends[-1].inject_frame(json.dumps(frame).encode())
    await rig.queen.send(good)
    await rig.until(lambda: len(rig.heard) == 1)

    assert rig.heard[0].id == good.id
    assert rig.queen.dials == 1  # No reconnection was needed.


async def test_a_new_connection_that_dies_while_catching_up_is_mended_again(
    rig: LinkRig,
) -> None:
    await _online(rig)
    rig.after.fail_times = 1  # The next connection dies while the trail is being shipped.

    rig.queen.drop()
    await rig.until(lambda: rig.queen.dials == 3 and rig.link.is_online)

    assert 0.5 in rig.clock.sleeps  # A link that dies at once is not redialled in a hot loop.


async def test_a_closed_link_refuses_sends_and_ends_receive(tmp_path: Path) -> None:
    rig = LinkRig(tmp_path)
    rig.start()
    await _online(rig)

    await rig.link.close()
    await rig.link.close()  # Idempotent.
    # A WebSocket's own close ends its pending read; a memory pair's does not, so the peer closes.
    await rig.queen.close_current()
    await rig.pump_ended()  # `receive` ends only now, after the close.

    with pytest.raises(TransportClosedError):
        await rig.link.send(rig.wire.result())
    assert not rig.link.is_online
    await rig.stop()


async def test_a_link_closed_between_attempts_dials_no_more_and_its_receive_ends(
    rig: LinkRig,
) -> None:
    await _online(rig)
    # The Queen refuses every dial: the link backs off and tries again, and again.
    rig.queen.fail_dials = 1_000_000
    rig.queen.drop()
    await rig.until(lambda: rig.queen.dials >= 4)

    await rig.link.close()
    await rig.pump_ended()  # The recovery saw the close at its next attempt: no cancel was needed.
    dials = rig.queen.dials
    for _ in range(20):
        await asyncio.sleep(0)

    assert rig.queen.dials == dials  # Not one more, though the Queen is still refusing.


class _SleepingClock(AutoClock):
    """An AutoClock whose backoff really waits, until the test advances it (it never does)."""

    def __init__(self) -> None:
        """Create the clock; `asleep` is set once something waits on it."""
        super().__init__()
        self.asleep = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        """Record `seconds` and wait for an `advance` past it, as a plain FakeClock does."""
        self.sleeps.append(seconds)
        self.asleep.set()
        await FakeClock.sleep(self, seconds)


async def test_closing_a_link_wakes_a_redial_asleep_between_attempts(tmp_path: Path) -> None:
    # Regression (phase 11 open item): `close` used to leave the backoff to run out (up to 30 s).
    clock = _SleepingClock()
    rig = LinkRig(tmp_path, clock=clock)
    rig.queen.fail_dials = 1_000_000
    rig.start()
    async with asyncio.timeout(5.0):
        await clock.asleep.wait()  # The first dial failed and its backoff has begun.

    await rig.link.close()
    await rig.pump_ended()  # At once: the clock never moved, so only the close could end it.

    assert rig.queen.dials == 1
    await rig.stop()


async def test_a_link_closed_while_a_dial_is_in_flight_does_not_keep_the_connection_it_gets(
    rig: LinkRig,
) -> None:
    await _online(rig)
    await _cut(rig)
    await rig.until(lambda: rig.queen.dials == 2)  # A dial is in flight, held back.
    seen = len(rig.phases)

    await rig.link.close()
    rig.queen.release_dials()  # The dial that was in flight connects, to a link nobody holds.
    await rig.pump_ended()

    assert not rig.link.is_online
    assert LinkPhase.ONLINE not in rig.phases[seen:]  # It never went back up after being closed.
    assert not rig.queen.warden_ends[-1].is_connected  # And what it dialled was closed, not kept.


async def test_cancelling_the_reader_during_a_recovery_ends_it_cleanly(tmp_path: Path) -> None:
    queen = ScriptedQueen()
    rig = LinkRig(tmp_path, queen)
    rig.start()
    await _online(rig)
    await _cut(rig)
    await rig.until(lambda: queen.dials == 2)  # Dialling, and the Queen cannot be reached.

    await rig.stop()  # Closing the link and cancelling the reader is how a Warden stops.

    assert not rig.pump_is_running
