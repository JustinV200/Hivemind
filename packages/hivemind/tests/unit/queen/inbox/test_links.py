"""Tests for hivemind.queen.inbox.links: LinkReaders, one reader task per attached Warden link.

Every test drives real `waggle.transport.memory.MemoryTransport` pairs: the Warden's end sends, the
link's own reader reads the Queen's end. Waiting is always on state (what a reader has queued,
whether its task is done), never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/queen/inbox/links.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.inbox.links for the module under test.
    - tests.unit.queen.test_queen_liveness_stall for the same readers under a real Queen.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import pytest
from builders.supervision import make_telemetry

from hivemind.queen.inbox.links import LinkReaders, Pulse
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import WardenId, new_hive_id, new_node_id, new_warden_id
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.transport.memory import MemoryTransport

_POLL_LIMIT = 500  # Loop turns a state wait may take before the test fails instead of hanging.
_IDLE_S = 5.0  # The quiet interval a wait gives up after: builders.queen's heartbeat interval.


@dataclass(frozen=True, slots=True)
class _Link:
    """One attached link under test: the Warden's id, both ends, and the Warden's address."""

    warden_id: WardenId
    queen_end: MemoryTransport
    warden_end: MemoryTransport
    hop: Hop


def _link(clock: FakeClock) -> _Link:
    """Build one MemoryTransport pair standing in for a Warden's link to the Queen."""
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    warden_id = new_warden_id(clock)
    hop = Hop(sender=warden_id, recipient=new_hive_id(clock), node_id=new_node_id(clock))
    return _Link(warden_id=warden_id, queen_end=queen_end, warden_end=warden_end, hop=hop)


def _heartbeat(interval_s: float = 5.0) -> Heartbeat:
    """A routine Heartbeat from an active Warden, declaring `interval_s` as its cadence."""
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=interval_s,
    )


async def _send_heartbeats(link: _Link, clock: FakeClock, count: int) -> None:
    """Send `count` Heartbeats from `link`'s Warden end, each a second after the one before."""
    for _ in range(count):
        clock.advance(1.0)
        await link.warden_end.send(wrap(_heartbeat(), link.hop, clock=clock))


async def _until(condition: Callable[[], bool]) -> None:
    """Yield the event loop until `condition()` holds, or fail after `_POLL_LIMIT` turns."""
    for _ in range(_POLL_LIMIT):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


async def test_drain_takes_everything_queued_on_every_link_in_arrival_order() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    first, second = _link(clock), _link(clock)
    await readers.add(first.warden_id, first.queen_end)
    await readers.add(second.warden_id, second.queen_end)
    await _send_heartbeats(first, clock, 3)
    await _send_heartbeats(second, clock, 2)
    await _until(lambda: readers.queued(first.warden_id) == 3)
    await _until(lambda: readers.queued(second.warden_id) == 2)

    items = readers.drain()

    principals = [item.principal for item in items]
    assert principals == [first.warden_id] * 3 + [second.warden_id] * 2
    # Oldest first within one link: the Attendant sees the backlog in the order it was sent.
    assert [item.received_at for item in items[:3]] == sorted(
        item.received_at for item in items[:3]
    )
    assert readers.queued(first.warden_id) == readers.queued(second.warden_id) == 0
    await readers.aclose()


async def test_a_flooding_link_never_starves_another_link_and_is_bounded_per_drain() -> None:
    # A queue of four: the flooding Warden's backlog is far larger than any one drain may take.
    clock = FakeClock()
    readers = LinkReaders(queue_size=4)
    flooder, quiet = _link(clock), _link(clock)
    await readers.add(flooder.warden_id, flooder.queen_end)
    await readers.add(quiet.warden_id, quiet.queen_end)
    await _send_heartbeats(flooder, clock, 40)

    # Two "ticks" in a row: each drain holds the quiet link's envelope and at most four floods.
    for _ in range(2):
        await _send_heartbeats(quiet, clock, 1)
        await _until(lambda: readers.queued(flooder.warden_id) == 4)
        await _until(lambda: readers.queued(quiet.warden_id) == 1)

        items = readers.drain()

        assert [item.principal for item in items].count(quiet.warden_id) == 1
        assert [item.principal for item in items].count(flooder.warden_id) == 4
    await readers.aclose()


async def test_a_full_queue_parks_its_reader_and_leaves_the_rest_with_the_transport() -> None:
    clock = FakeClock()
    readers = LinkReaders(queue_size=2)
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    await _send_heartbeats(link, clock, 5)
    await _until(lambda: readers.queued(link.warden_id) == 2)

    # Several more loop turns change nothing: the reader waits for room, it never buffers more.
    for _ in range(10):
        await asyncio.sleep(0)
    assert readers.queued(link.warden_id) == 2

    drained = [len(readers.drain())]
    await _until(lambda: readers.queued(link.warden_id) == 2)
    drained.append(len(readers.drain()))
    await _until(lambda: readers.queued(link.warden_id) == 1)
    drained.append(len(readers.drain()))
    assert drained == [2, 2, 1]
    await readers.aclose()


async def test_heard_is_the_newest_heartbeat_each_link_delivered_handled_or_not() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    await _send_heartbeats(link, clock, 3)
    await _until(lambda: readers.queued(link.warden_id) == 3)

    # Heard before any drain: the newest Heartbeat counts even while it still waits in the queue.
    assert readers.heard() == {link.warden_id: Pulse(sent_at=clock.now(), interval_s=5.0)}
    readers.drain()
    assert readers.heard() == {link.warden_id: Pulse(sent_at=clock.now(), interval_s=5.0)}
    await readers.aclose()


async def test_heard_never_moves_backwards_for_an_older_heartbeat_heard_later() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    await _send_heartbeats(link, clock, 1)
    newest = clock.now()
    # A Heartbeat stamped earlier than the one already heard, delivered after it, declaring a
    # cadence of its own: neither its time nor its interval replaces the newest one's.
    late = FakeClock(start=newest - timedelta(seconds=30))
    await link.warden_end.send(wrap(_heartbeat(interval_s=60.0), link.hop, clock=late))
    await _until(lambda: readers.queued(link.warden_id) == 2)

    assert readers.heard() == {link.warden_id: Pulse(sent_at=newest, interval_s=5.0)}
    await readers.aclose()


async def test_heard_carries_the_interval_the_newest_heartbeat_declared() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    await link.warden_end.send(wrap(_heartbeat(interval_s=15.0), link.hop, clock=clock))
    clock.advance(15.0)
    await link.warden_end.send(wrap(_heartbeat(interval_s=30.0), link.hop, clock=clock))
    await _until(lambda: readers.queued(link.warden_id) == 2)

    # A Warden that slows its own cadence is judged by the one it declared last.
    assert readers.heard() == {link.warden_id: Pulse(sent_at=clock.now(), interval_s=30.0)}
    await readers.aclose()


async def test_an_invalid_payload_frame_is_skipped_and_the_link_reads_on() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    # A frame with a readable id but a payload its model refuses: InvalidPayloadError, pair open.
    wire = json.loads(Codec().encode(wrap(_heartbeat(), link.hop, clock=clock)))
    wire["payload"] = {"unexpected": 1}
    link.queen_end.inject_frame(json.dumps(wire, sort_keys=True, separators=(",", ":")).encode())
    await _send_heartbeats(link, clock, 1)

    await _until(lambda: readers.queued(link.warden_id) == 1)

    [item] = readers.drain()
    assert isinstance(item.payload, Heartbeat)
    await readers.aclose()


async def test_a_closed_link_ends_with_no_item_and_its_reader_finishes() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    before = asyncio.all_tasks()
    await readers.add(link.warden_id, link.queen_end)
    await _send_heartbeats(link, clock, 1)
    await link.warden_end.close()
    [reader] = asyncio.all_tasks() - before

    await _until(reader.done)

    # The Heartbeat sent before the close is still delivered; the end marker yields nothing.
    [item] = readers.drain()
    assert isinstance(item.payload, Heartbeat)
    assert readers.drain() == []
    await readers.aclose()
    assert asyncio.all_tasks() == before


async def test_remove_and_aclose_leave_no_reader_task_pending() -> None:
    clock = FakeClock()
    readers = LinkReaders(queue_size=1)
    first, second = _link(clock), _link(clock)
    before = asyncio.all_tasks()
    await readers.add(first.warden_id, first.queen_end)
    await readers.add(second.warden_id, second.queen_end)
    # One reader parked on a full queue, one waiting on its transport: both must be reaped.
    await _send_heartbeats(first, clock, 3)
    await _until(lambda: readers.queued(first.warden_id) == 1)
    assert len(asyncio.all_tasks() - before) == 2

    await readers.remove(first.warden_id)
    assert len(asyncio.all_tasks() - before) == 1
    await readers.aclose()

    assert asyncio.all_tasks() == before
    assert readers.queued(first.warden_id) == readers.queued(second.warden_id) == 0


async def test_wait_returns_on_a_queued_envelope_on_wake_and_reports_stop() -> None:
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    stop, wake = asyncio.Event(), asyncio.Event()
    before = asyncio.all_tasks()

    await _send_heartbeats(link, clock, 1)
    assert await asyncio.wait_for(_wait(readers, stop, wake, clock), timeout=5.0)
    readers.drain()
    wake.set()
    assert await asyncio.wait_for(_wait(readers, stop, wake, clock), timeout=5.0)
    stop.set()
    assert not await asyncio.wait_for(_wait(readers, stop, wake, clock), timeout=5.0)

    # Every throwaway waiter a wait started is reaped before it returns.
    assert asyncio.all_tasks() == before
    await readers.aclose()


async def test_wait_returns_after_a_quiet_interval_with_nothing_arriving() -> None:
    # Every Warden silent, no wake: only the interval on her own clock ends the wait.
    clock = FakeClock()
    readers = LinkReaders()
    link = _link(clock)
    await readers.add(link.warden_id, link.queen_end)
    stop, wake = asyncio.Event(), asyncio.Event()
    waiting = asyncio.ensure_future(_wait(readers, stop, wake, clock))
    for _ in range(10):
        await asyncio.sleep(0)
    assert not waiting.done()  # Short of the interval, nothing ends it.

    clock.advance(_IDLE_S)

    assert await asyncio.wait_for(waiting, timeout=5.0)
    assert readers.drain() == []
    await readers.aclose()


async def _wait(
    readers: LinkReaders, stop: asyncio.Event, wake: asyncio.Event, clock: FakeClock
) -> bool:
    """`readers.wait` on `clock` with the quiet interval every test here uses."""
    return await readers.wait(stop, wake, clock=clock, idle_s=_IDLE_S)


def test_a_queue_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="queue_size"):
        LinkReaders(queue_size=0)
