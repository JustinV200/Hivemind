"""Tests for waggle.transport.memory: the in-process pair's delivery, close, drop and refusals.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises MemoryTransport.pair through the
    plain and signed codec fixtures: ordered delivery both ways, the clean-close and drop
    semantics of the transport contract, and every codec refusal (garbage, oversized,
    tampered, unsigned, invalid payload) driven through inject_frame so the close codes of
    waggle.transport.base are checked on a real transport.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.memory for the module under test.
    - waggle.transport.base for the contract and the close-code table asserted here.
    - conftest.py for the codec and envelope fixtures.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest

from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import (
    ConnectionLostError,
    FrameTooLargeError,
    InvalidPayloadError,
    InvalidSignatureError,
    MalformedFrameError,
    MissingSignatureError,
    TransportClosedError,
)
from waggle.messages.control.protocol import Ping, Pong
from waggle.transport.base import (
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_NORMAL,
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
)
from waggle.transport.memory import MemoryTransport

MakeEnvelope = Callable[..., Envelope]
Pair = tuple[MemoryTransport, MemoryTransport]

WAIT_S = 5.0  # Bounds every await that could hang; a healthy pair answers in microseconds.
TINY_LIMIT = 64  # Smaller than any real frame, so a normal envelope trips the size check.
EXCHANGE_COUNT = 5  # Enough envelopes to prove order, few enough to read in a failure.


@pytest.fixture
def plain_pair(plain_codec: Codec) -> Pair:
    """Two ends that neither sign nor verify: the in-process policy."""
    return MemoryTransport.pair(plain_codec, plain_codec)


async def _collect(transport: MemoryTransport) -> list[Envelope]:
    """Drain receive() to the end of the stream."""
    async with asyncio.timeout(WAIT_S):
        return [envelope async for envelope in transport.receive()]


async def _next(transport: MemoryTransport) -> Envelope:
    """The next envelope receive() yields; the generator is closed afterwards."""
    async with asyncio.timeout(WAIT_S):
        receiver = transport.receive()
        try:
            return await anext(receiver)
        finally:
            await receiver.aclose()


def _wire(codec: Codec, envelope: Envelope) -> dict[str, object]:
    """The frame as a dict, so a test can alter one key and re-encode it."""
    parsed: dict[str, object] = json.loads(codec.encode(envelope))
    return parsed


# ──────────────────────────────────────────────────────────────────────────────
# Delivery and clean close
# ──────────────────────────────────────────────────────────────────────────────


async def test_pair_exchanges_envelopes_in_order_in_both_directions(
    plain_pair: Pair, make_envelope: MakeEnvelope, fake_clock: object
) -> None:
    end_a, end_b = plain_pair
    pings = [make_envelope(Ping()) for _ in range(EXCHANGE_COUNT)]
    pongs = [
        make_envelope(Pong(received_at=ping.sent_at), correlation_id=ping.id) for ping in pings
    ]

    for ping in pings:
        await end_a.send(ping)
    for pong in pongs:
        await end_b.send(pong)
    await end_a.close()
    await end_b.close()

    assert await _collect(end_b) == pings
    assert await _collect(end_a) == pongs


async def test_close_ends_the_peers_receive_normally(plain_pair: Pair) -> None:
    end_a, end_b = plain_pair

    await end_a.close()

    assert await _collect(end_b) == []
    assert await _collect(end_a) == []
    assert not end_a.is_connected
    assert not end_b.is_connected
    assert end_a.close_code == CLOSE_NORMAL


async def test_frames_queued_before_a_clean_close_are_still_delivered(
    plain_pair: Pair, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = plain_pair
    envelope = make_envelope()

    await end_a.send(envelope)
    await end_a.close()

    assert await _collect(end_b) == [envelope]


async def test_frames_queued_before_this_ends_own_close_are_still_delivered(
    plain_pair: Pair, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = plain_pair
    envelope = make_envelope()
    await end_a.send(envelope)

    # websockets drains what arrived before a close; the pair does the same, without waiting.
    await end_b.close()

    assert await _collect(end_b) == [envelope]
    assert await _collect(end_b) == []


async def test_send_after_close_raises_transport_closed_on_both_ends(
    plain_pair: Pair, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = plain_pair
    await end_a.close()

    with pytest.raises(TransportClosedError, match="This end") as own:
        await end_a.send(make_envelope())
    with pytest.raises(TransportClosedError, match="peer end") as peer:
        await end_b.send(make_envelope())

    assert own.value.code == "waggle.transport.closed"
    assert peer.value.code == "waggle.transport.closed"


async def test_close_is_idempotent_and_connect_is_a_no_op(plain_pair: Pair) -> None:
    end_a, end_b = plain_pair
    await end_a.connect()
    assert end_a.is_connected

    await end_a.close()
    await end_a.close()

    # One sentinel reaches the peer: its stream ends once, and ends cleanly.
    assert await _collect(end_b) == []
    assert await _collect(end_b) == []


# ──────────────────────────────────────────────────────────────────────────────
# Drop
# ──────────────────────────────────────────────────────────────────────────────


async def test_drop_raises_connection_lost_on_a_pending_receive_and_every_later_call(
    plain_pair: Pair, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = plain_pair
    pending = asyncio.ensure_future(_collect(end_b))
    await asyncio.sleep(0)  # let the receive block on its empty inbox

    end_a.drop()

    with pytest.raises(ConnectionLostError) as caught:
        await pending
    assert caught.value.code == "waggle.transport.connection_lost"
    with pytest.raises(ConnectionLostError):
        await end_a.send(make_envelope())
    with pytest.raises(ConnectionLostError):
        await end_b.send(make_envelope())
    with pytest.raises(ConnectionLostError):
        await _collect(end_a)
    with pytest.raises(ConnectionLostError):
        await _collect(end_b)
    assert not end_a.is_connected
    assert not end_b.is_connected
    assert end_a.close_code is None


# ──────────────────────────────────────────────────────────────────────────────
# Refused frames
# ──────────────────────────────────────────────────────────────────────────────


async def test_injected_garbage_raises_malformed_and_closes_with_1002(
    plain_pair: Pair, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = plain_pair
    end_b.inject_frame(b"not a frame")

    with pytest.raises(MalformedFrameError):
        await _next(end_b)

    assert not end_b.is_connected
    assert end_b.close_code == CLOSE_PROTOCOL_ERROR
    # The refusing end closed; the peer sees a protocol close, which is a lost connection.
    with pytest.raises(TransportClosedError):
        await end_b.send(make_envelope())
    with pytest.raises(ConnectionLostError, match=str(CLOSE_PROTOCOL_ERROR)):
        await end_a.send(make_envelope())
    with pytest.raises(ConnectionLostError):
        await _collect(end_a)
    with pytest.raises(ConnectionLostError):
        await _collect(end_b)


async def test_a_clean_close_never_hides_the_protocol_code(plain_pair: Pair) -> None:
    end_a, end_b = plain_pair
    end_b.inject_frame(b"not a frame")
    with pytest.raises(MalformedFrameError):
        await _next(end_b)

    await end_a.close()

    assert end_a.close_code == CLOSE_PROTOCOL_ERROR


async def test_an_oversized_envelope_fails_at_send_and_nothing_is_delivered(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = MemoryTransport.pair(Codec(max_frame_bytes=TINY_LIMIT), plain_codec)

    with pytest.raises(FrameTooLargeError) as caught:
        await end_a.send(make_envelope())
    await end_a.close()

    assert caught.value.code == "waggle.codec.too_large"
    assert end_a.is_connected is False
    assert await _collect(end_b) == []


async def test_an_oversized_injected_frame_is_refused_with_1009(plain_codec: Codec) -> None:
    _, end_b = MemoryTransport.pair(plain_codec, Codec(max_frame_bytes=TINY_LIMIT))
    end_b.inject_frame(b"x" * (TINY_LIMIT + 1))

    with pytest.raises(FrameTooLargeError):
        await _next(end_b)

    assert end_b.close_code == CLOSE_MESSAGE_TOO_BIG


async def test_a_signed_pair_rejects_a_tampered_frame_with_1008(
    signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = MemoryTransport.pair(signed_codec, signed_codec)
    good = make_envelope()
    await end_a.send(good)
    assert (await _next(end_b)).id == good.id
    # A minor bump keeps the frame well-formed and acceptable, so only the signature can fail.
    frame = signed_codec.encode(good)
    tampered = frame.replace(b'"version":"1.0"', b'"version":"1.1"')
    assert tampered != frame

    end_b.inject_frame(tampered)

    with pytest.raises(InvalidSignatureError) as caught:
        await _next(end_b)
    assert caught.value.code == "waggle.signature.invalid"
    assert end_b.close_code == CLOSE_POLICY_VIOLATION


async def test_a_verifying_end_rejects_an_unsigned_frame_with_1008(
    plain_codec: Codec, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = MemoryTransport.pair(plain_codec, signed_codec)

    await end_a.send(make_envelope())

    with pytest.raises(MissingSignatureError):
        await _next(end_b)
    assert end_b.close_code == CLOSE_POLICY_VIOLATION


async def test_an_invalid_payload_keeps_the_pair_open_and_receive_continues(
    plain_pair: Pair, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    end_a, end_b = plain_pair
    wire = _wire(plain_codec, make_envelope())
    wire["payload"] = {"unexpected": 1}  # extra="forbid": a readable id, a payload that fails
    end_b.inject_frame(json.dumps(wire).encode())

    with pytest.raises(InvalidPayloadError) as caught:
        await _next(end_b)

    assert caught.value.code == "waggle.codec.invalid_payload"
    assert caught.value.message_id == wire["id"]
    assert caught.value.sender == wire["sender"]
    assert caught.value.kind == "control.ping"
    assert end_b.is_connected
    assert end_b.close_code is None
    good = make_envelope()
    await end_a.send(good)
    assert await _next(end_b) == good
