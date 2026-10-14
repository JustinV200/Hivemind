"""Tests for hivemind.wardens.offline.outbox: the durable queue, the held questions, the replay.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/outbox.py (codingrules section 3). What a cut-off Warden
    could not send waits on disk (events, in order, surviving a restart), or in memory (a
    question). A replay sends one entry at a time and acknowledges it only after the link took it;
    a request goes out in a fresh envelope, an event under the id it was queued with; an entry that
    no longer decodes is dropped rather than replayed for ever.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.outbox for the module under test.
    - waggle.outbox for the queue this wraps.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.offline import make_wire

from hivemind.wardens.offline import OfflineOutbox, OfflineRefusedError, open_outbox
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError, OutboxCorruptError
from waggle.ids import new_message_id
from waggle.messages.base import MAX_MESSAGE_AGE_S
from waggle.outbox import Outbox
from waggle.outbox.log import append_record, encode_put
from waggle.transport.memory import MemoryTransport


class _DeadLink:
    """A transport whose every send fails, as a link that has just gone does."""

    async def send(self, envelope: Envelope) -> None:
        raise ConnectionLostError("The link is gone.")


def _open(directory: Path, clock: FakeClock) -> OfflineOutbox:
    """Open the queue under `directory` with the default codec."""
    return open_outbox(directory, Codec(), clock)


async def _drain(queue: OfflineOutbox, link: MemoryTransport) -> None:
    """Replay everything queued through `link`, under the lock the contract asks for."""
    async with queue.lock:
        while await queue.replay_one(link):
            pass


async def _received(end: MemoryTransport, count: int) -> list[Envelope]:
    """Read `count` frames off `end`."""
    frames: list[Envelope] = []
    inbox = end.receive()
    try:
        while len(frames) < count:
            frames.append(await anext(inbox))
    finally:
        await inbox.aclose()
    return frames


async def test_queued_events_are_replayed_in_order_under_their_own_ids(tmp_path: Path) -> None:
    clock = FakeClock()
    wire, queue = make_wire(clock), _open(tmp_path, clock)
    first, second, third = wire.result(), wire.alarm(), wire.progress()
    mine, theirs = MemoryTransport.pair(Codec(), Codec())
    async with queue.lock:
        for envelope in (first, second, third):
            await queue.queue(envelope)

    await _drain(queue, mine)

    assert [e.id for e in await _received(theirs, 3)] == [first.id, second.id, third.id]
    assert queue.queued() == 0


async def test_the_queue_survives_a_restart_of_the_warden(tmp_path: Path) -> None:
    clock = FakeClock()
    wire = make_wire(clock)
    queue = _open(tmp_path, clock)
    result = wire.result()
    async with queue.lock:
        await queue.queue(result)

    reopened = _open(tmp_path, clock)  # The Warden's process restarted: the file is all it has.

    assert reopened.queued() == 1
    mine, theirs = MemoryTransport.pair(Codec(), Codec())
    await _drain(reopened, mine)
    assert [e.id for e in await _received(theirs, 1)] == [result.id]


async def test_a_message_queued_twice_is_replayed_once(tmp_path: Path) -> None:
    clock = FakeClock()
    queue, result = _open(tmp_path, clock), make_wire(clock).result()
    async with queue.lock:
        await queue.queue(result)
        await queue.queue(result)

    assert queue.queued() == 1


async def test_an_entry_is_acknowledged_only_after_the_link_took_it(tmp_path: Path) -> None:
    clock = FakeClock()
    queue = _open(tmp_path, clock)
    async with queue.lock:
        await queue.queue(make_wire(clock).result())

    with pytest.raises(ConnectionLostError):
        async with queue.lock:
            await queue.replay_one(_DeadLink())  # type: ignore[arg-type]

    # Still queued: the next reconnection sends it, and the Queen's replay window drops a copy.
    assert queue.queued() == 1


async def test_a_replayed_request_goes_out_in_a_fresh_envelope(tmp_path: Path) -> None:
    clock = FakeClock()
    wire, queue = make_wire(clock), _open(tmp_path, clock)
    request = wire.forage_request()
    mine, theirs = MemoryTransport.pair(Codec(), Codec())
    async with queue.lock:
        await queue.queue(request)
    clock.advance(60.0)  # Time passes while the Warden is cut off.

    await _drain(queue, mine)

    (sent,) = await _received(theirs, 1)
    assert sent.id != request.id  # The Queen's replay window would drop the old id unread.
    assert sent.sent_at > request.sent_at
    assert sent.payload == request.payload  # The question is the same; only the envelope is new.
    assert (sent.sender, sent.recipient, sent.node_id) == (
        request.sender,
        request.recipient,
        request.node_id,
    )


async def test_a_replayed_event_keeps_its_id_and_its_time(tmp_path: Path) -> None:
    clock = FakeClock()
    wire, queue = make_wire(clock), _open(tmp_path, clock)
    result = wire.result()
    mine, theirs = MemoryTransport.pair(Codec(), Codec())
    async with queue.lock:
        await queue.queue(result)
    clock.advance(60.0)

    await _drain(queue, mine)

    (sent,) = await _received(theirs, 1)
    assert (sent.id, sent.sent_at) == (result.id, result.sent_at)


async def test_a_message_too_large_to_keep_is_refused_so_the_sender_hears_it(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    queue = open_outbox(tmp_path, Codec(max_frame_bytes=2_048), clock)
    result = make_wire(clock).result()  # Well past what a tiny codec stores unsigned.
    big = result.model_copy(
        update={"payload": result.payload.model_copy(update={"summary": "x" * 1_900})}
    )

    with pytest.raises(OfflineRefusedError, match="too large"):
        async with queue.lock:
            await queue.queue(big)

    assert queue.queued() == 0


async def test_a_disk_that_cannot_take_a_message_is_refused_not_lost_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    queue = _open(tmp_path, clock)

    def full_disk(self: Outbox, envelope: Envelope) -> bool:
        raise OSError("No space left on device.")

    monkeypatch.setattr(Outbox, "append", full_disk)

    with pytest.raises(OfflineRefusedError, match="could not be written"):
        async with queue.lock:
            await queue.queue(make_wire(clock).result())


async def test_entries_older_than_the_freshness_window_are_expired(tmp_path: Path) -> None:
    clock = FakeClock()
    queue, wire = _open(tmp_path, clock), make_wire(clock)
    async with queue.lock:
        await queue.queue(wire.result())
    clock.advance(MAX_MESSAGE_AGE_S + 1.0)
    async with queue.lock:
        await queue.queue(wire.alarm())  # Queued after the wait: fresh.

    assert await queue.expire() == 1
    assert queue.queued() == 1


async def test_an_entry_that_no_longer_decodes_is_dropped_not_replayed_for_ever(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    wire = make_wire(clock)
    # A frame an upgrade could no longer read: the log holds it, the codec refuses it.
    append_record(tmp_path / "outbox.jsonl", encode_put(new_message_id(clock), "not a frame"))
    queue = _open(tmp_path, clock)
    good = wire.result()
    async with queue.lock:
        await queue.queue(good)
    mine, theirs = MemoryTransport.pair(Codec(), Codec())

    await _drain(queue, mine)

    assert [e.id for e in await _received(theirs, 1)] == [good.id]
    assert queue.queued() == 0


async def test_held_questions_go_out_oldest_first_and_only_once(tmp_path: Path) -> None:
    clock = FakeClock()
    wire, queue = make_wire(clock), _open(tmp_path, clock)
    first, second = wire.question(), wire.question()
    mine, theirs = MemoryTransport.pair(Codec(), Codec())
    async with queue.lock:
        queue.hold(first)
        queue.hold(second)
        queue.hold(first)  # The same question held again: still one.
        assert queue.held() == 2
        assert await queue.release_held(mine) == 2

    assert [e.id for e in await _received(theirs, 2)] == [first.id, second.id]
    assert queue.held() == 0


async def test_a_question_the_link_would_not_take_stays_held(tmp_path: Path) -> None:
    clock = FakeClock()
    queue = _open(tmp_path, clock)
    async with queue.lock:
        queue.hold(make_wire(clock).question())

    with pytest.raises(ConnectionLostError):
        async with queue.lock:
            await queue.release_held(_DeadLink())  # type: ignore[arg-type]

    assert queue.held() == 1


def test_a_corrupt_queue_file_is_refused_at_open_not_papered_over(tmp_path: Path) -> None:
    (tmp_path / "outbox.jsonl").write_bytes(b'{"op": "put"}\nthis is not json\n{"op": "ack"}\n')

    with pytest.raises(OutboxCorruptError):
        _open(tmp_path, FakeClock())
