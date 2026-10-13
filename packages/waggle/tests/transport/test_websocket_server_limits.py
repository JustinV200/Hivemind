"""Tests for WebSocketServer's limits over real loopback connections: the 4029 close and replays.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Starts a WebSocketServer with ServerLimits on an
    OS-assigned loopback port, injects a FakeClock so time is exact, and drives accepted
    WebSocketTransports from a raw websockets client that sends the frames a well-behaved peer
    never would. Covers a flood closed with 4029 and reported once, frames that fail validation
    still counting, the bucket refilling with the clock, connections isolated from each other, a
    signed frame replayed on a second connection dropped and reported, duplicates on one
    connection, the window expiring, a server with no limits changing nothing, and an unknown
    option refused. The pure pieces are tested in test_replay.py, test_ceiling.py and
    test_limits.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.websocket_server and waggle.transport.websocket for the code under test.
    - conftest.py for the ``refusals`` and ``on_refusal`` fixtures.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import (
    ConnectionLostError,
    FrameCeilingError,
    InvalidPayloadError,
    TransportClosedError,
)
from waggle.transport.base import CLOSE_CODE_FOR, CLOSE_FRAME_CEILING
from waggle.transport.ceiling import CeilingLimits
from waggle.transport.limits import (
    ConnectionRefusal,
    RefusalHandler,
    RefusalKind,
    ServerLimits,
)
from waggle.transport.replay import ReplayLimits
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

MakeEnvelope = Callable[..., Envelope]

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
BURST = 3  # A tiny bucket: three frames, the fourth is over the ceiling.
CEILING = CeilingLimits(max_frames_per_minute=60, burst=BURST)  # One token a second.
WINDOW_S = 60.0
FRAME_CEILING_REASON = "waggle.transport.frame_ceiling"


async def _started(
    codec: Codec,
    clock: FakeClock,
    on_refusal: RefusalHandler | None,
    limits: ServerLimits | None,
) -> WebSocketServer:
    """A started loopback listener enforcing ``limits`` on ``clock``'s time."""
    server = WebSocketServer(codec, limits=limits, on_refusal=on_refusal, clock=clock)
    await server.start()
    return server


async def _dial_raw(server: WebSocketServer) -> ClientConnection:
    """A bare websockets client, for sending frames no transport would."""
    async with asyncio.timeout(WAIT_S):
        return await connect(server.uri, proxy=None)


async def _accept(server: WebSocketServer) -> WebSocketTransport:
    """The next connection the server accepted."""
    async with asyncio.timeout(WAIT_S):
        listing = server.connections()
        try:
            return await anext(listing)
        finally:
            await listing.aclose()


async def _take(transport: WebSocketTransport, count: int) -> list[Envelope]:
    """The next ``count`` envelopes receive() yields; the generator is closed afterwards."""
    received: list[Envelope] = []
    async with asyncio.timeout(WAIT_S):
        receiver = transport.receive()
        try:
            async for envelope in receiver:
                received.append(envelope)
                if len(received) == count:
                    break
        finally:
            await receiver.aclose()
    return received


async def _close_seen_by(raw: ClientConnection) -> tuple[int, str]:
    """The close code and reason the raw client received when its next recv fails."""
    async with asyncio.timeout(WAIT_S):
        with pytest.raises(ConnectionClosed) as caught:
            await raw.recv()
    assert caught.value.rcvd is not None
    return caught.value.rcvd.code, caught.value.rcvd.reason


@pytest.fixture
async def listener(
    plain_codec: Codec, fake_clock: FakeClock, on_refusal: RefusalHandler
) -> AsyncIterator[WebSocketServer]:
    """A listener with the default replay window and a tiny frame ceiling, closed afterwards."""
    server = await _started(
        plain_codec, fake_clock, on_refusal, ServerLimits(replay=None, ceiling=CEILING)
    )
    yield server
    await server.close()


# ──────────────────────────────────────────────────────────────────────────────
# The frame ceiling
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_flood_is_closed_with_4029_and_reported_once(
    listener: WebSocketServer,
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
) -> None:
    raw = await _dial_raw(listener)
    accepted = await _accept(listener)
    envelopes = [make_envelope() for _ in range(BURST + 4)]
    try:
        for envelope in envelopes:
            await raw.send(plain_codec.encode(envelope))

        received: list[Envelope] = []
        with pytest.raises(FrameCeilingError) as caught:
            async with asyncio.timeout(WAIT_S):
                async for envelope in accepted.receive():
                    received.append(envelope)

        # Exactly the burst got through; the next frame was over the ceiling.
        assert received == envelopes[:BURST]
        assert caught.value.code == FRAME_CEILING_REASON
        assert await _close_seen_by(raw) == (CLOSE_FRAME_CEILING, FRAME_CEILING_REASON)
        # Reported once, naming the node the connection had spoken for, and the connection is over.
        assert [(r.kind, r.node_id, r.envelope_id) for r in refusals] == [
            (RefusalKind.FRAME_CEILING, envelopes[0].node_id, None)
        ]
        assert not accepted.is_connected
        with pytest.raises(TransportClosedError):
            await accepted.send(make_envelope())
    finally:
        await raw.close()


async def test_frames_that_fail_validation_still_count_against_the_ceiling(
    listener: WebSocketServer,
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
) -> None:
    raw = await _dial_raw(listener)
    accepted = await _accept(listener)
    try:
        wire: dict[str, object] = json.loads(plain_codec.encode(make_envelope()))
        wire["payload"] = {"unexpected": 1}  # extra="forbid": a readable id, a bad payload
        bad_frame = json.dumps(wire).encode()
        for _ in range(BURST + 1):
            await raw.send(bad_frame)

        # Each bad frame costs the listener a decode, so each spends a token even though none
        # yields an envelope: the connection survives them (invalid payload keeps it open)...
        for _ in range(BURST):
            with pytest.raises(InvalidPayloadError):
                await _take(accepted, 1)
        # ...until the bucket runs dry, and then the next frame is a flood, valid or not.
        with pytest.raises(FrameCeilingError):
            await _take(accepted, 1)

        assert await _close_seen_by(raw) == (CLOSE_FRAME_CEILING, FRAME_CEILING_REASON)
        assert [r.kind for r in refusals] == [RefusalKind.FRAME_CEILING]
    finally:
        await raw.close()


async def test_the_bucket_refills_with_the_clock(
    listener: WebSocketServer,
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    fake_clock: FakeClock,
) -> None:
    raw = await _dial_raw(listener)
    accepted = await _accept(listener)
    try:
        for _ in range(BURST):
            await raw.send(plain_codec.encode(make_envelope()))
        assert len(await _take(accepted, BURST)) == BURST  # the bucket is now empty

        # One second at 60 a minute earns exactly one token: one more frame gets through...
        fake_clock.advance(1.0)
        await raw.send(plain_codec.encode(make_envelope()))
        assert len(await _take(accepted, 1)) == 1

        # ...and with no time passing the very next one is over the ceiling.
        await raw.send(plain_codec.encode(make_envelope()))
        with pytest.raises(FrameCeilingError):
            await _take(accepted, 1)
    finally:
        await raw.close()


async def test_one_flooding_connection_does_not_touch_another_or_a_later_one(
    listener: WebSocketServer,
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
) -> None:
    flooder = await _dial_raw(listener)
    flooded = await _accept(listener)
    bystander = await _dial_raw(listener)
    kept = await _accept(listener)
    try:
        for _ in range(BURST + 1):
            await flooder.send(plain_codec.encode(make_envelope()))
        for _ in range(BURST):
            await bystander.send(plain_codec.encode(make_envelope()))

        with pytest.raises(FrameCeilingError):
            async with asyncio.timeout(WAIT_S):
                _ = [envelope async for envelope in flooded.receive()]
        # The bystander has a bucket of its own, still full, and is still connected.
        assert len(await _take(kept, BURST)) == BURST
        assert kept.is_connected

        # A fresh connection after the flooder was closed starts with a full bucket again.
        redialled = await _dial_raw(listener)
        later = await _accept(listener)
        try:
            for _ in range(BURST):
                await redialled.send(plain_codec.encode(make_envelope()))
            assert len(await _take(later, BURST)) == BURST
        finally:
            await redialled.close()
        assert len(refusals) == 1
    finally:
        await flooder.close()
        await bystander.close()


async def test_a_flooding_client_sees_a_lost_link_and_dials_afresh_with_a_full_bucket(
    listener: WebSocketServer,
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    fake_clock: FakeClock,
) -> None:
    client = WebSocketClientTransport(
        listener.uri, plain_codec, fake_clock, options=DialOptions(max_attempts=1)
    )
    async with asyncio.timeout(WAIT_S):
        await client.connect()
    accepted = await _accept(listener)
    try:
        for _ in range(BURST + 2):
            await client.send(make_envelope())
        with pytest.raises(FrameCeilingError):
            async with asyncio.timeout(WAIT_S):
                _ = [envelope async for envelope in accepted.receive()]

        # The dialler is told by an ordinary lost link, 4029 in its text, so its loop reconnects
        # with its usual backoff and replays its outbox (spec section 10).
        with pytest.raises(ConnectionLostError, match=str(CLOSE_FRAME_CEILING)):
            async with asyncio.timeout(WAIT_S):
                _ = [envelope async for envelope in client.receive()]
        assert not client.is_connected

        # Nothing is held against it: a fresh dial gets a full bucket of its own.
        async with asyncio.timeout(WAIT_S):
            await client.connect()
        again = await _accept(listener)
        for _ in range(BURST):
            await client.send(make_envelope())
        assert len(await _take(again, BURST)) == BURST
    finally:
        await client.close()


# ──────────────────────────────────────────────────────────────────────────────
# The replay window
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_signed_envelope_replayed_on_a_second_connection_is_dropped_and_reported(
    signed_codec: Codec,
    make_envelope: MakeEnvelope,
    fake_clock: FakeClock,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    server = await _started(signed_codec, fake_clock, on_refusal, ServerLimits(ceiling=None))
    original, fresh = make_envelope(), make_envelope()
    captured = signed_codec.encode(original)  # the bytes an eavesdropper would have kept
    first = await _dial_raw(server)
    first_end = await _accept(server)
    try:
        await first.send(captured)
        assert [e.id for e in await _take(first_end, 1)] == [original.id]
        await first.close()

        # A fresh connection (a reconnect, or an attacker) sends the same signed bytes again,
        # then a genuinely new frame. The window is the listener's, so the replay is caught.
        second = await _dial_raw(server)
        second_end = await _accept(server)
        try:
            await second.send(captured)
            await second.send(signed_codec.encode(fresh))

            assert [e.id for e in await _take(second_end, 1)] == [fresh.id]

            assert refusals == [
                ConnectionRefusal(
                    node_id=original.node_id,
                    kind=RefusalKind.REPLAY,
                    envelope_id=original.id,
                    at=fake_clock.now(),
                )
            ]
            # A replay is skipped, not punished: the connection stays open.
            assert second_end.is_connected
        finally:
            await second.close()
    finally:
        await first.close()
        await server.close()


async def test_a_duplicate_on_the_same_connection_is_skipped_and_the_next_frame_is_read(
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    fake_clock: FakeClock,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    server = await _started(plain_codec, fake_clock, on_refusal, ServerLimits(ceiling=None))
    original, fresh = make_envelope(), make_envelope()
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        for envelope in (original, original, original, fresh):
            await raw.send(plain_codec.encode(envelope))

        assert [e.id for e in await _take(accepted, 2)] == [original.id, fresh.id]

        assert [(r.kind, r.envelope_id) for r in refusals] == [
            (RefusalKind.REPLAY, original.id)
        ] * 2
    finally:
        await raw.close()
        await server.close()


async def test_the_replay_window_expires_with_the_clock(
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    fake_clock: FakeClock,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    limits = ServerLimits(replay=ReplayLimits(window_s=WINDOW_S), ceiling=None)
    server = await _started(plain_codec, fake_clock, on_refusal, limits)
    envelope = make_envelope()
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        await raw.send(plain_codec.encode(envelope))
        assert len(await _take(accepted, 1)) == 1

        # A full window later the id is forgotten, so the same frame is delivered again; that
        # bound is what keeps the listener's memory finite (spec section 9).
        fake_clock.advance(WINDOW_S)
        await raw.send(plain_codec.encode(envelope))
        assert [e.id for e in await _take(accepted, 1)] == [envelope.id]
        assert refusals == []
    finally:
        await raw.close()
        await server.close()


async def test_duplicates_are_dropped_without_a_callback_too(
    plain_codec: Codec, make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    server = await _started(plain_codec, fake_clock, None, ServerLimits(ceiling=None))
    original, fresh = make_envelope(), make_envelope()
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        for envelope in (original, original, fresh):
            await raw.send(plain_codec.encode(envelope))

        assert [e.id for e in await _take(accepted, 2)] == [original.id, fresh.id]
    finally:
        await raw.close()
        await server.close()


# ──────────────────────────────────────────────────────────────────────────────
# Nothing changes without limits
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "limits", [None, ServerLimits(replay=None, ceiling=None)], ids=["no-limits", "both-off"]
)
async def test_a_listener_without_active_limits_delivers_duplicates_and_floods(
    plain_codec: Codec,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
    limits: ServerLimits | None,
) -> None:
    # The clock never advances here, so no bucket could refill: any refusal would show.
    server = await _started(plain_codec, FakeClock(), on_refusal, limits)
    envelope = make_envelope()
    count = 5 * BURST  # far past the tiny ceiling the other tests use
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        for _ in range(count):
            await raw.send(plain_codec.encode(envelope))  # the same frame, over and over

        assert len(await _take(accepted, count)) == count
        assert accepted.is_connected
        assert refusals == []
    finally:
        await raw.close()
        await server.close()


def test_a_misspelt_option_is_refused_instead_of_silently_ignored(plain_codec: Codec) -> None:
    # A typo'd ssl_context that was ignored would serve plaintext where TLS was meant.
    with pytest.raises(TypeError, match="ssl_contxt"):
        WebSocketServer(plain_codec, ssl_contxt=None)  # type: ignore[call-arg]  # the point


def test_the_frame_ceiling_close_code_is_4029_and_no_decode_failure_maps_to_it() -> None:
    assert CLOSE_FRAME_CEILING == 4029
    assert CLOSE_FRAME_CEILING not in CLOSE_CODE_FOR.values()
