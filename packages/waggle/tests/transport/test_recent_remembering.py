"""Tests for waggle.transport.recent.remembering: a Transport that can send what it sent again.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises RememberingTransport over a recording
    in-memory stand-in for a connection: a frame is remembered only after its send returned and
    only when the node says it must arrive, everything else about the connection is the inner
    transport's own, and `resend` sends the frames due on a new connection oldest first under their
    own ids, remembers them afresh, stops at a failed send with every frame still held, and gives
    each send a timeout.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.recent.remembering for the module under test.
    - test_recent_ring.py for the memory it fills.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError
from waggle.messages.base import WaggleMessage
from waggle.messages.control import Ping, Pong
from waggle.transport.recent import RecentFrames, RememberingTransport, every_frame
from waggle.transport.recent import remembering as remembering_module

Make = Callable[..., Envelope]


class _Wire:
    """A stand-in connection: it keeps what it is sent, and can fail or hang on cue."""

    def __init__(self) -> None:
        self.sent: list[Envelope] = []
        self.fail_on: int | None = None  # The 1-based send that raises a lost link.
        self.hang = False  # Every send waits forever.
        self.is_connected = True
        self.closes = 0
        self.connects = 0
        self._arriving: list[Envelope] = []

    async def connect(self) -> None:
        self.connects += 1

    async def send(self, envelope: Envelope) -> None:
        if self.hang:
            await asyncio.Event().wait()
        if self.fail_on == len(self.sent) + 1:
            raise ConnectionLostError("The stand-in link dropped.")
        self.sent.append(envelope)

    async def receive(self) -> AsyncIterator[Envelope]:
        for envelope in self._arriving:
            yield envelope

    async def close(self) -> None:
        self.closes += 1


def _only_pings(message: WaggleMessage) -> bool:
    """A node that has to get Pings through and does not care about anything else."""
    return isinstance(message, Ping)


def _remembering(
    wire: _Wire, clock: FakeClock, wanted: Callable[[WaggleMessage], bool] = every_frame
) -> tuple[RememberingTransport, RecentFrames]:
    """A wrapper over `wire` with a ring of its own, on `clock`."""
    recent = RecentFrames()
    return RememberingTransport(wire, recent, clock, wanted), recent


async def test_a_frame_that_was_sent_is_remembered_and_reaches_the_inner_connection(
    make_envelope: Make, fake_clock: FakeClock
) -> None:
    wire = _Wire()
    link, recent = _remembering(wire, fake_clock)
    frame = make_envelope()

    await link.send(frame)

    assert wire.sent == [frame]
    assert recent.due(lost_at=fake_clock.monotonic(), now=fake_clock.monotonic()) == (frame,)


async def test_a_frame_whose_send_raised_is_not_remembered(
    make_envelope: Make, fake_clock: FakeClock
) -> None:
    wire = _Wire()
    wire.fail_on = 1
    link, recent = _remembering(wire, fake_clock)

    # Nothing went on the connection, so it is the caller's to queue and not this ring's to resend.
    with pytest.raises(ConnectionLostError):
        await link.send(make_envelope())

    assert len(recent) == 0


async def test_a_message_the_node_does_not_need_to_arrive_passes_through_unremembered(
    make_envelope: Make, fake_clock: FakeClock
) -> None:
    wire = _Wire()
    link, recent = _remembering(wire, fake_clock, _only_pings)
    ping = make_envelope(Ping())
    pong = make_envelope(Pong(received_at=fake_clock.now()), correlation_id=ping.id)

    await link.send(ping)
    await link.send(pong)

    assert wire.sent == [ping, pong]  # Both went out.
    assert recent.due(lost_at=0.0, now=0.0) == (ping,)  # Only the one that had to arrive is held.


async def test_the_rest_of_the_transport_is_the_inner_transports_own(fake_clock: FakeClock) -> None:
    wire = _Wire()
    link, _ = _remembering(wire, fake_clock)

    await link.connect()
    await link.close()
    wire.is_connected = False

    assert (wire.connects, wire.closes, link.is_connected) == (1, 1, False)
    assert [e async for e in link.receive()] == []


async def test_resend_sends_the_frames_due_oldest_first_under_their_own_ids(
    make_envelope: Make, fake_clock: FakeClock
) -> None:
    lost = _Wire()
    link, recent = _remembering(lost, fake_clock)
    sent = [make_envelope() for _ in range(3)]
    for frame in sent:
        await link.send(frame)
        fake_clock.advance(1.0)
    lost_at = fake_clock.monotonic()
    fake_clock.advance(5.0)  # The new connection comes up a few seconds after the loss was found.
    fresh = _Wire()
    again = RememberingTransport(fresh, recent, fake_clock, every_frame)

    count = await again.resend(lost_at)

    # The very envelopes, not copies in new envelopes: the receiver's window knows them by id.
    assert count == 3 and [e.id for e in fresh.sent] == [e.id for e in sent]
    assert fresh.sent == sent


async def test_a_frame_sent_again_is_remembered_afresh_for_the_next_loss(
    make_envelope: Make, fake_clock: FakeClock
) -> None:
    lost = _Wire()
    link, recent = _remembering(lost, fake_clock)
    sent = make_envelope()
    await link.send(sent)
    fake_clock.advance(100.0)  # Far enough that, by its first send alone, it would have aged out.
    again = RememberingTransport(_Wire(), recent, fake_clock, every_frame)

    await again.resend(lost_at=fake_clock.monotonic() - 50.0)
    resent_at = fake_clock.monotonic()
    fake_clock.advance(10.0)

    # The connection it was sent on is lost too, ten seconds on: the frame is due once more, since
    # it was last sent after the previous loss, not before it.
    assert recent.due(lost_at=resent_at + 10.0, now=fake_clock.monotonic() + 1.0) == (sent,)
    assert len(recent) == 1


async def test_resend_stops_at_a_failed_send_and_every_frame_is_still_held(
    make_envelope: Make, fake_clock: FakeClock
) -> None:
    lost = _Wire()
    link, recent = _remembering(lost, fake_clock)
    sent = [make_envelope() for _ in range(3)]
    for frame in sent:
        await link.send(frame)
    fresh = _Wire()
    fresh.fail_on = 2  # The new connection dies after the first frame.
    again = RememberingTransport(fresh, recent, fake_clock, every_frame)

    with pytest.raises(ConnectionLostError):
        await again.resend(lost_at=fake_clock.monotonic())

    assert fresh.sent == sent[:1] and len(recent) == 3
    # The next connection makes the whole re-send again, in the order it found.
    third = _Wire()
    third_link = RememberingTransport(third, recent, fake_clock, every_frame)
    await third_link.resend(lost_at=fake_clock.monotonic())
    assert third.sent == sent


async def test_resend_gives_each_frame_a_timeout(
    make_envelope: Make, fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    lost = _Wire()
    link, recent = _remembering(lost, fake_clock)
    await link.send(make_envelope())
    monkeypatch.setattr(remembering_module, "RESEND_TIMEOUT_S", 0.05)
    stuck = _Wire()
    stuck.hang = True  # A connection that takes no frame.
    again = RememberingTransport(stuck, recent, fake_clock, every_frame)

    with pytest.raises(TimeoutError):
        await again.resend(lost_at=fake_clock.monotonic())

    assert len(recent) == 1


async def test_resend_with_nothing_due_sends_nothing(fake_clock: FakeClock) -> None:
    wire = _Wire()
    link, _ = _remembering(wire, fake_clock)

    assert await link.resend(lost_at=fake_clock.monotonic()) == 0
    assert wire.sent == []


def test_every_message_is_one_the_default_test_accepts(fake_clock: FakeClock) -> None:
    # `every_frame` is the test for a sender whose own path already holds only what must arrive.
    assert every_frame(Ping()) is True
    assert every_frame(Pong(received_at=fake_clock.now())) is True
