"""Tests for waggle.outbox_replay: draining an outbox through a transport, poison and expiry.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Replays a real Outbox from tmp_path through a
    MemoryTransport pair (in-order delivery, acks, signing at send time, idempotent replay),
    through a small fake Transport whose link dies after a fixed number of sends (the unsent
    tail stays pending and the report names the error), and past the two kinds of poison entry
    (a frame the sending codec refuses, and one this node no longer decodes). expire_older_than
    is driven with the FakeClock the envelopes were stamped by.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.outbox_replay for the module under test.
    - test_outbox.py for the queue itself.
    - tests/contracts/test_transport_contract.py for replay after a dropped link over both
      transports.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import pytest

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.messages.base import MAX_MESSAGE_AGE_S
from waggle.messages.control import ErrorMessage
from waggle.outbox import Outbox
from waggle.outbox_log import append_record, encode_put
from waggle.outbox_replay import ReplayReport, expire_older_than, replay_outbox
from waggle.transport.memory import MemoryTransport

MakeEnvelope = Callable[..., Envelope]

WAIT_S = 5.0  # Bounds every await that could hang; a memory pair answers in microseconds.
QUEUE_SIZE = 5  # Enough entries that a link lost after two leaves a visible tail.
SENDS_BEFORE_LOSS = 2
LONG_MESSAGE = "x" * 400  # Makes an ErrorMessage frame clearly larger than a Ping's.
SLACK_BYTES = 16  # Room above a Ping's frame so a tight codec admits it and nothing bigger.
OLD_S = 100.0  # How long the stale entries wait before the fresh one is queued.
RECENT_S = 50.0  # How long the fresh entry waits before the expiry runs.
WINDOW_S = 120.0  # The freshness window: older than the stale entries, younger than the fresh.


class LinkLosingTransport:
    """A Transport whose link dies after a fixed number of sends; it records what it carried.

    Implements the Transport protocol honestly (codingrules 14.4): a send that returns was
    carried, a send that raises was not, and once the link is lost every later call raises.
    Nothing ever arrives on it, so ``receive`` is an empty stream.
    """

    def __init__(self, sends_before_loss: int) -> None:
        """Set how many sends succeed before the link is lost.

        Args:
            sends_before_loss: Sends carried before every later one raises ConnectionLostError.
        """
        self.sent: list[Envelope] = []  # What was carried, in order.
        self.inbound: list[Envelope] = []  # What receive yields: nothing, no peer speaks here.
        self._sends_before_loss = sends_before_loss
        self._lost = False
        self._closed = False

    @property
    def is_connected(self) -> bool:
        """Whether a send may still succeed.

        Returns:
            True until the link is lost or the transport is closed.
        """
        return not self._lost and not self._closed

    async def connect(self) -> None:
        """Do nothing: the fake is connected from construction.

        Returns:
            None.
        """
        return None

    async def send(self, envelope: Envelope) -> None:
        """Record ``envelope`` as carried, or lose the link once the quota is spent.

        Args:
            envelope: What the caller wants carried.

        Raises:
            TransportClosedError: ``close`` was called.
            ConnectionLostError: The link is lost, now or earlier.
        """
        if self._closed:
            raise TransportClosedError("The fake transport is closed; nothing can be sent.")
        if self._lost or len(self.sent) >= self._sends_before_loss:
            self._lost = True
            raise ConnectionLostError("The fake transport's link was lost.")
        self.sent.append(envelope)

    async def receive(self) -> AsyncGenerator[Envelope, None]:
        """Yield what a peer sent, which on this fake is nothing.

        Returns:
            An empty stream that ends at once, as after a clean close.

        Raises:
            ConnectionLostError: The link was lost.
        """
        if self._lost:
            raise ConnectionLostError("The fake transport's link was lost.")
        for envelope in self.inbound:
            yield envelope

    async def close(self) -> None:
        """Mark the fake closed; a second call does nothing.

        Returns:
            None.
        """
        self._closed = True


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """Where the outbox under test keeps its log."""
    return tmp_path / "outbox.jsonl"


async def _received_by(transport: MemoryTransport) -> list[Envelope]:
    """Everything the end received up to the peer's close."""
    async with asyncio.timeout(WAIT_S):
        return [envelope async for envelope in transport.receive()]


def _write_put(path: Path, codec: Codec, envelope: Envelope) -> None:
    """Write the put record the outbox would have written for ``envelope``."""
    append_record(path, encode_put(envelope.id, codec.encode(envelope).decode("utf-8")))


def _write_unknown_kind_put(path: Path, codec: Codec, envelope: Envelope) -> None:
    """Write a put whose frame names a kind this node has no model for (an upgrade removed it)."""
    wire: dict[str, object] = json.loads(codec.encode(envelope))
    wire["kind"] = "control.unregistered"
    append_record(path, encode_put(envelope.id, json.dumps(wire)))


# ──────────────────────────────────────────────────────────────────────────────
# Replay
# ──────────────────────────────────────────────────────────────────────────────


async def test_replay_sends_every_pending_envelope_in_order_and_acks_each(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    sender, receiver = MemoryTransport.pair(plain_codec, plain_codec)
    outbox = Outbox(log_path)
    queued = [make_envelope() for _ in range(QUEUE_SIZE)]
    for envelope in queued:
        outbox.append(envelope)

    report = await replay_outbox(outbox, sender)
    await sender.close()

    assert report == ReplayReport(sent=QUEUE_SIZE, poisoned=(), stopped_by=None)
    assert report.is_complete
    assert await _received_by(receiver) == queued
    assert len(outbox) == 0
    assert len(Outbox(log_path)) == 0


async def test_replay_signs_at_send_time_and_keeps_each_id_and_sent_at(
    log_path: Path, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    sender, receiver = MemoryTransport.pair(signed_codec, signed_codec)
    outbox = Outbox(log_path, codec=signed_codec)
    queued = [make_envelope(), make_envelope()]
    for envelope in queued:
        outbox.append(envelope)

    report = await replay_outbox(outbox, sender)
    await sender.close()

    received = await _received_by(receiver)
    assert report.sent == len(queued)
    # The verifying end accepted them, so they were signed on the way out of the outbox, and a
    # receiver can still dedupe by id and judge freshness by sent_at.
    assert all(envelope.signature is not None for envelope in received)
    assert [(e.id, e.sent_at) for e in received] == [(e.id, e.sent_at) for e in queued]


async def test_replaying_twice_sends_nothing_the_second_time(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    sender, receiver = MemoryTransport.pair(plain_codec, plain_codec)
    outbox = Outbox(log_path)
    outbox.append(make_envelope())
    first = await replay_outbox(outbox, sender)
    assert first.sent == 1

    second = await replay_outbox(outbox, sender)
    await sender.close()

    assert second == ReplayReport(sent=0, poisoned=(), stopped_by=None)
    assert len(await _received_by(receiver)) == 1


async def test_a_link_lost_mid_replay_leaves_exactly_the_unsent_tail_pending(
    log_path: Path, make_envelope: MakeEnvelope
) -> None:
    outbox = Outbox(log_path)
    queued = [make_envelope() for _ in range(QUEUE_SIZE)]
    for envelope in queued:
        outbox.append(envelope)
    flaky = LinkLosingTransport(sends_before_loss=SENDS_BEFORE_LOSS)

    report = await replay_outbox(outbox, flaky)

    assert report.sent == SENDS_BEFORE_LOSS
    assert report.poisoned == ()
    assert isinstance(report.stopped_by, ConnectionLostError)
    assert not report.is_complete
    assert not flaky.is_connected
    assert flaky.sent == queued[:SENDS_BEFORE_LOSS]
    assert outbox.pending_ids() == tuple(envelope.id for envelope in queued[SENDS_BEFORE_LOSS:])
    # The tail survives a reopen, as after a crash, and goes out on the next link in order.
    healthy = LinkLosingTransport(sends_before_loss=QUEUE_SIZE)
    resumed = await replay_outbox(Outbox(log_path), healthy)
    assert resumed == ReplayReport(
        sent=QUEUE_SIZE - SENDS_BEFORE_LOSS, poisoned=(), stopped_by=None
    )
    assert healthy.sent == queued[SENDS_BEFORE_LOSS:]


# ──────────────────────────────────────────────────────────────────────────────
# Poison entries
# ──────────────────────────────────────────────────────────────────────────────


async def test_an_entry_the_sending_codec_refuses_is_acked_and_reported_not_retried(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    ping_before, ping_after = make_envelope(), make_envelope()
    long_reply = make_envelope(
        ErrorMessage(
            code="hive.test.too_long",
            message=LONG_MESSAGE,
            failed_kind=None,
            is_retryable=False,
        ),
        correlation_id=ping_before.id,
    )
    # The outbox's own limit admits all three; the link's codec is tighter than the outbox's.
    outbox = Outbox(log_path)
    for envelope in (ping_before, long_reply, ping_after):
        outbox.append(envelope)
    tight = Codec(max_frame_bytes=len(plain_codec.encode(ping_after)) + SLACK_BYTES)
    sender, receiver = MemoryTransport.pair(tight, plain_codec)

    report = await replay_outbox(outbox, sender)
    await sender.close()

    assert report == ReplayReport(sent=2, poisoned=(long_reply.id,), stopped_by=None)
    assert await _received_by(receiver) == [ping_before, ping_after]
    assert len(outbox) == 0


async def test_an_entry_this_node_no_longer_decodes_is_acked_and_reported(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    good_before, stale, good_after = make_envelope(), make_envelope(), make_envelope()
    _write_put(log_path, plain_codec, good_before)
    _write_unknown_kind_put(log_path, plain_codec, stale)
    _write_put(log_path, plain_codec, good_after)
    outbox = Outbox(log_path)
    sender, receiver = MemoryTransport.pair(plain_codec, plain_codec)

    report = await replay_outbox(outbox, sender)
    await sender.close()

    assert report == ReplayReport(sent=2, poisoned=(stale.id,), stopped_by=None)
    assert await _received_by(receiver) == [good_before, good_after]
    assert len(outbox) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Expiry
# ──────────────────────────────────────────────────────────────────────────────


def test_expire_older_than_acks_only_entries_older_than_the_window(
    log_path: Path, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    outbox = Outbox(log_path)
    stale = [make_envelope(), make_envelope()]
    for envelope in stale:
        outbox.append(envelope)
    fake_clock.advance(OLD_S)
    fresh = make_envelope()
    outbox.append(fresh)
    fake_clock.advance(RECENT_S)

    expired = expire_older_than(outbox, fake_clock, max_age_s=WINDOW_S)

    assert expired == len(stale)
    assert outbox.pending_ids() == (fresh.id,)


def test_expire_keeps_an_entry_exactly_max_age_old_and_defaults_to_the_receivers_window(
    log_path: Path, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    outbox = Outbox(log_path)
    on_the_edge = make_envelope()
    outbox.append(on_the_edge)
    fake_clock.advance(MAX_MESSAGE_AGE_S)

    assert expire_older_than(outbox, fake_clock) == 0
    assert outbox.pending_ids() == (on_the_edge.id,)

    # One second later every receiver would drop it unread, so the send is saved.
    fake_clock.advance(1)
    assert expire_older_than(outbox, fake_clock) == 1
    assert len(outbox) == 0


def test_expire_leaves_an_entry_it_cannot_decode_for_replay_to_report(
    log_path: Path, plain_codec: Codec, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    old, stale = make_envelope(), make_envelope()
    _write_put(log_path, plain_codec, old)
    _write_unknown_kind_put(log_path, plain_codec, stale)
    outbox = Outbox(log_path)
    fake_clock.advance(MAX_MESSAGE_AGE_S + 1)

    assert expire_older_than(outbox, fake_clock) == 1

    assert outbox.pending_ids() == (stale.id,)
