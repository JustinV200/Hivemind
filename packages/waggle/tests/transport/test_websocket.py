"""Tests for waggle.transport.websocket over a real loopback listener: delivery, close, refusals.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Starts a WebSocketServer on an OS-assigned
    loopback port and dials it with a WebSocketClientTransport, then drives the accepted and
    dialled WebSocketTransports through the contract: ordered delivery both ways, clean close
    from either side, and every refusal a raw websockets client can provoke (a text frame,
    garbage, an oversized frame, a tampered or unsigned frame, an invalid payload), asserting
    the close code the raw client sees. The client's backoff and reconnect live in
    test_websocket_client.py; the listener's own properties in test_websocket_server.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.websocket for the module under test.
    - waggle.transport.base for the contract and the close codes asserted here.
    - conftest.py for the codec and envelope fixtures.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from waggle.clock import FakeClock
from waggle.codec import MAX_FRAME_BYTES, Codec
from waggle.envelope import PROTOCOL_MAJOR, PROTOCOL_MINOR, PROTOCOL_VERSION, Envelope
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
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_client import WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

MakeEnvelope = Callable[..., Envelope]

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
EXCHANGE_COUNT = 50  # Enough frames each way to catch a reordering or a dropped frame.
TWO_MEBIBYTES = 2 * MAX_FRAME_BYTES


@pytest.fixture
async def server(plain_codec: Codec) -> AsyncIterator[WebSocketServer]:
    """A started loopback listener with the plain codec, closed after the test."""
    server = WebSocketServer(plain_codec)
    await server.start()
    yield server
    await server.close()


@pytest.fixture
async def signed_server(signed_codec: Codec) -> AsyncIterator[WebSocketServer]:
    """A started loopback listener that signs and requires signatures."""
    server = WebSocketServer(signed_codec)
    await server.start()
    yield server
    await server.close()


async def _dial(server: WebSocketServer, codec: Codec) -> WebSocketClientTransport:
    """A connected client transport for ``server``; the FakeClock never sleeps on success."""
    client = WebSocketClientTransport(server.uri, codec, FakeClock(), max_attempts=1)
    async with asyncio.timeout(WAIT_S):
        await client.connect()
    return client


async def _accept(server: WebSocketServer) -> WebSocketTransport:
    """The next connection the server accepted."""
    async with asyncio.timeout(WAIT_S):
        listing = server.connections()
        try:
            return await anext(listing)
        finally:
            await listing.aclose()


async def _dial_raw(server: WebSocketServer) -> ClientConnection:
    """A bare websockets client, for sending frames no transport would."""
    async with asyncio.timeout(WAIT_S):
        return await connect(server.uri, proxy=None)


async def _collect(transport: WebSocketTransport | WebSocketClientTransport) -> list[Envelope]:
    """Drain receive() to the end of the stream."""
    async with asyncio.timeout(WAIT_S):
        return [envelope async for envelope in transport.receive()]


async def _take(
    transport: WebSocketTransport | WebSocketClientTransport, count: int
) -> list[Envelope]:
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


async def _close_code_seen_by(raw: ClientConnection) -> tuple[int, str]:
    """The close code and reason the raw client received when its next recv fails."""
    async with asyncio.timeout(WAIT_S):
        with pytest.raises(ConnectionClosed) as caught:
            await raw.recv()
    assert caught.value.rcvd is not None
    return caught.value.rcvd.code, caught.value.rcvd.reason


# ──────────────────────────────────────────────────────────────────────────────
# Delivery and clean close
# ──────────────────────────────────────────────────────────────────────────────


async def test_client_and_server_exchange_fifty_envelopes_each_way_in_order(
    server: WebSocketServer, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    client = await _dial(server, plain_codec)
    accepted = await _accept(server)
    await accepted.connect()  # a no-op on a connection the accept already opened
    assert accepted.is_connected
    pings = [make_envelope(Ping()) for _ in range(EXCHANGE_COUNT)]
    pongs = [
        make_envelope(Pong(received_at=ping.sent_at), correlation_id=ping.id) for ping in pings
    ]

    for ping in pings:
        await client.send(ping)
    assert await _take(accepted, EXCHANGE_COUNT) == pings
    for pong in pongs:
        await accepted.send(pong)
    assert await _take(client, EXCHANGE_COUNT) == pongs

    assert accepted.remote_address.startswith("127.0.0.1:")
    await client.close()
    await accepted.close()


async def test_clean_close_from_the_client_ends_the_servers_receive(
    server: WebSocketServer, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    client = await _dial(server, plain_codec)
    accepted = await _accept(server)

    await client.close()
    await client.close()

    assert await _collect(accepted) == []
    assert not accepted.is_connected
    assert not client.is_connected
    with pytest.raises(TransportClosedError):
        await client.send(make_envelope())
    with pytest.raises(TransportClosedError, match="closed the connection cleanly"):
        await accepted.send(make_envelope())


async def test_clean_close_from_the_server_side_ends_the_clients_receive(
    server: WebSocketServer, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    client = await _dial(server, plain_codec)
    accepted = await _accept(server)

    await accepted.close()
    await accepted.close()

    assert await _collect(client) == []
    with pytest.raises(TransportClosedError):
        await client.send(make_envelope())


async def test_server_close_with_a_connected_client_ends_the_clients_receive(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    server = WebSocketServer(plain_codec)
    await server.start()
    client = await _dial(server, plain_codec)
    await _accept(server)

    async with asyncio.timeout(WAIT_S):
        await server.close()

    assert await _collect(client) == []
    assert not client.is_connected
    with pytest.raises(TransportClosedError):
        await client.send(make_envelope())


# ──────────────────────────────────────────────────────────────────────────────
# Refused frames
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_text_frame_is_malformed_and_closed_with_1002(
    server: WebSocketServer, make_envelope: MakeEnvelope
) -> None:
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        await raw.send("a text frame")

        with pytest.raises(MalformedFrameError, match="text frame"):
            await _take(accepted, 1)

        assert await _close_code_seen_by(raw) == (CLOSE_PROTOCOL_ERROR, "waggle.codec.malformed")
        assert not accepted.is_connected
        with pytest.raises(TransportClosedError):
            await accepted.send(make_envelope())
        with pytest.raises(ConnectionLostError):
            await _collect(accepted)
    finally:
        await raw.close()


async def test_a_garbage_binary_frame_is_closed_with_1002(server: WebSocketServer) -> None:
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        await raw.send(b"\xff not a frame")

        with pytest.raises(MalformedFrameError):
            await _take(accepted, 1)

        assert (await _close_code_seen_by(raw))[0] == CLOSE_PROTOCOL_ERROR
    finally:
        await raw.close()


async def test_a_two_mebibyte_frame_is_refused_with_1009(server: WebSocketServer) -> None:
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        await raw.send(b"x" * TWO_MEBIBYTES)

        with pytest.raises(FrameTooLargeError, match=str(MAX_FRAME_BYTES)):
            await _take(accepted, 1)

        assert (await _close_code_seen_by(raw))[0] == CLOSE_MESSAGE_TOO_BIG
        assert not accepted.is_connected
        await accepted.close()
    finally:
        await raw.close()


async def test_signed_codecs_accept_a_signed_frame_and_reject_a_tampered_one(
    signed_server: WebSocketServer, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    client = await _dial(signed_server, signed_codec)
    accepted = await _accept(signed_server)
    good = make_envelope()
    await client.send(good)
    assert (await _take(accepted, 1))[0].id == good.id
    await client.close()
    raw = await _dial_raw(signed_server)
    tampered_end = await _accept(signed_server)
    try:
        # Built from the live constants, not a hardcoded pair, so this stays correct across a
        # future protocol minor bump instead of silently no-op'ing past PROTOCOL_VERSION "1.1".
        frame = signed_codec.encode(good)
        bumped_version = f"{PROTOCOL_MAJOR}.{PROTOCOL_MINOR + 1}"
        tampered = frame.replace(
            f'"version":"{PROTOCOL_VERSION}"'.encode(), f'"version":"{bumped_version}"'.encode()
        )
        await raw.send(tampered)

        with pytest.raises(InvalidSignatureError):
            await _take(tampered_end, 1)

        assert await _close_code_seen_by(raw) == (
            CLOSE_POLICY_VIOLATION,
            "waggle.signature.invalid",
        )
    finally:
        await raw.close()


async def test_an_unsigned_frame_to_a_verifying_server_is_closed_with_1008(
    signed_server: WebSocketServer, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    client = await _dial(signed_server, plain_codec)
    accepted = await _accept(signed_server)

    await client.send(make_envelope())

    with pytest.raises(MissingSignatureError):
        await _take(accepted, 1)
    # The client sees a protocol close: a lost connection, never a clean end of stream, on its
    # receive and on its next send alike.
    with pytest.raises(ConnectionLostError, match=str(CLOSE_POLICY_VIOLATION)):
        await _collect(client)
    with pytest.raises(ConnectionLostError, match="was lost before the frame"):
        await client.send(make_envelope())
    assert not client.is_connected


async def test_an_invalid_payload_keeps_the_connection_open(
    server: WebSocketServer, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    raw = await _dial_raw(server)
    accepted = await _accept(server)
    try:
        wire: dict[str, object] = json.loads(plain_codec.encode(make_envelope()))
        wire["payload"] = {"unexpected": 1}  # extra="forbid": a readable id, a bad payload
        await raw.send(json.dumps(wire).encode())

        with pytest.raises(InvalidPayloadError) as caught:
            await _take(accepted, 1)

        assert caught.value.message_id == wire["id"]
        assert accepted.is_connected
        good = make_envelope()
        await raw.send(plain_codec.encode(good))
        assert await _take(accepted, 1) == [good]
        await accepted.close()
        assert (await _close_code_seen_by(raw))[0] == CLOSE_NORMAL
    finally:
        await raw.close()
