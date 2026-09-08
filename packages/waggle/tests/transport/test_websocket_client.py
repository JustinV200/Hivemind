"""Tests for waggle.transport.websocket_client: the dial, its backoff, and reconnecting.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises WebSocketClientTransport on its own:
    the URI and attempt-count checks at construction, the not-connected errors, the backoff
    sequence against a closed port driven through a recording FakeClock that a helper task
    advances, ConnectFailedError after max_attempts, and dialling afresh after a close or after
    the server restarts on the same port.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.websocket_client for the module under test.
    - test_websocket.py for the frames a connected client carries.
    - test_loop.py for the same recording-clock technique on TickLoop's backoff.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable

import pytest

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectFailedError, ConnectionLostError, TransportClosedError
from waggle.transport.websocket_client import (
    RECONNECT_MAX_S,
    WebSocketClientTransport,
)
from waggle.transport.websocket_server import WebSocketServer

MakeEnvelope = Callable[..., Envelope]

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
# A refused loopback dial takes about two seconds on Windows (the TCP stack retries the SYN), so
# the per-attempt open_timeout is what keeps a multi-attempt test under a second of real time.
DIAL_TIMEOUT_S = 0.2
CAPPED_ATTEMPTS = 8  # 0.5, 1, 2, 4, 8, 16, 30 (capped): seven waits between eight dials.


class RecordingClock(FakeClock):
    """A FakeClock that records every sleep and flags when a sleeper is waiting."""

    def __init__(self) -> None:
        """Create a RecordingClock with no sleeps recorded yet."""
        super().__init__()
        self.sleep_durations: list[float] = []
        self.asleep = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        """Record ``seconds``, wake the advancing helper, then sleep as a FakeClock does."""
        self.sleep_durations.append(seconds)
        self.asleep.set()
        await super().sleep(seconds)


async def _advance_through(clock: RecordingClock, sleeps: int) -> None:
    """Advance ``clock`` past each of the next ``sleeps`` sleeps as they are registered."""
    for _ in range(sleeps):
        await clock.asleep.wait()
        clock.asleep.clear()
        clock.advance(clock.sleep_durations[-1])


def _closed_port() -> int:
    """A loopback port nothing listens on: bound for an instant, then released."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def _started(codec: Codec, port: int = 0) -> WebSocketServer:
    """A started loopback listener on ``port`` (0 for OS-assigned)."""
    server = WebSocketServer(codec, port=port)
    await server.start()
    return server


async def _round_trip(
    client: WebSocketClientTransport, server: WebSocketServer, envelope: Envelope
) -> None:
    """Send ``envelope`` from the client and check the server's next connection receives it."""
    async with asyncio.timeout(WAIT_S):
        listing = server.connections()
        try:
            accepted = await anext(listing)
        finally:
            await listing.aclose()
        await client.send(envelope)
        receiver = accepted.receive()
        try:
            assert await anext(receiver) == envelope
        finally:
            await receiver.aclose()
        await accepted.close()


# ──────────────────────────────────────────────────────────────────────────────
# Construction and the not-connected state
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("uri", ["ws://example.org:9000", "http://127.0.0.1:9000", "ws://"])
def test_a_uri_waggle_may_not_dial_is_refused_at_construction(
    uri: str, plain_codec: Codec, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match=uri):
        WebSocketClientTransport(uri, plain_codec, fake_clock)


def test_wss_and_loopback_ws_uris_are_accepted(plain_codec: Codec, fake_clock: FakeClock) -> None:
    for uri in ("wss://queen.example.org:8443/waggle", "ws://localhost:9000", "ws://[::1]:9000"):
        assert WebSocketClientTransport(uri, plain_codec, fake_clock).uri == uri


def test_max_attempts_below_one_is_refused(plain_codec: Codec, fake_clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        WebSocketClientTransport("ws://127.0.0.1:9000", plain_codec, fake_clock, max_attempts=0)


async def test_send_and_receive_before_connect_raise_connection_lost(
    plain_codec: Codec, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    client = WebSocketClientTransport("ws://127.0.0.1:9000", plain_codec, fake_clock)
    assert not client.is_connected

    with pytest.raises(ConnectionLostError, match="not connected"):
        await client.send(make_envelope())
    with pytest.raises(ConnectionLostError, match="not connected"):
        async for _ in client.receive():
            pass


async def test_close_before_connect_makes_send_raise_transport_closed(
    plain_codec: Codec, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    client = WebSocketClientTransport("ws://127.0.0.1:9000", plain_codec, fake_clock)

    await client.close()
    await client.close()

    with pytest.raises(TransportClosedError, match="was closed"):
        await client.send(make_envelope())


# ──────────────────────────────────────────────────────────────────────────────
# Backoff
# ──────────────────────────────────────────────────────────────────────────────


async def test_backoff_follows_the_documented_sequence_then_connect_failed(
    plain_codec: Codec,
) -> None:
    clock = RecordingClock()
    uri = f"ws://127.0.0.1:{_closed_port()}"
    client = WebSocketClientTransport(
        uri, plain_codec, clock, max_attempts=4, open_timeout_s=DIAL_TIMEOUT_S
    )

    # The helper advances the fake clock past each backoff sleep as the dial registers it;
    # three sleeps separate four attempts, and the fourth failure ends the dial.
    async with asyncio.timeout(WAIT_S), asyncio.TaskGroup() as group:
        group.create_task(_advance_through(clock, 3))
        with pytest.raises(ConnectFailedError) as caught:
            await client.connect()

    assert clock.sleep_durations == [0.5, 1.0, 2.0]
    assert caught.value.code == "waggle.transport.connect_failed"
    assert uri in str(caught.value)
    assert "4 attempts" in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    assert not client.is_connected


async def test_backoff_is_capped_at_reconnect_max(plain_codec: Codec) -> None:
    clock = RecordingClock()
    client = WebSocketClientTransport(
        f"ws://127.0.0.1:{_closed_port()}",
        plain_codec,
        clock,
        max_attempts=CAPPED_ATTEMPTS,
        open_timeout_s=DIAL_TIMEOUT_S / 4,
    )

    async with asyncio.timeout(WAIT_S), asyncio.TaskGroup() as group:
        group.create_task(_advance_through(clock, CAPPED_ATTEMPTS - 1))
        with pytest.raises(ConnectFailedError):
            await client.connect()

    assert clock.sleep_durations == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, RECONNECT_MAX_S]


async def test_a_single_attempt_never_sleeps(plain_codec: Codec) -> None:
    clock = RecordingClock()
    client = WebSocketClientTransport(
        f"ws://127.0.0.1:{_closed_port()}",
        plain_codec,
        clock,
        max_attempts=1,
        open_timeout_s=DIAL_TIMEOUT_S,
    )

    async with asyncio.timeout(WAIT_S):
        with pytest.raises(ConnectFailedError, match="1 attempts"):
            await client.connect()

    assert clock.sleep_durations == []


# ──────────────────────────────────────────────────────────────────────────────
# Connecting again
# ──────────────────────────────────────────────────────────────────────────────


async def test_connect_is_a_no_op_while_connected_and_dials_afresh_after_close(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    server = await _started(plain_codec)
    client = WebSocketClientTransport(server.uri, plain_codec, FakeClock(), max_attempts=1)
    try:
        async with asyncio.timeout(WAIT_S):
            await client.connect()
            await client.connect()
        await _round_trip(client, server, make_envelope())

        await client.close()
        with pytest.raises(TransportClosedError):
            await client.send(make_envelope())
        async with asyncio.timeout(WAIT_S):
            await client.connect()

        assert client.is_connected
        await _round_trip(client, server, make_envelope())
        await client.close()
    finally:
        await server.close()


async def test_reconnect_after_the_server_restarts_on_the_same_port(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    first = await _started(plain_codec)
    port = first.port
    client = WebSocketClientTransport(first.uri, plain_codec, FakeClock(), max_attempts=1)
    async with asyncio.timeout(WAIT_S):
        await client.connect()
    await _round_trip(client, first, make_envelope())

    # The Hive Stand goes away: the client's stream ends and its next send fails.
    async with asyncio.timeout(WAIT_S):
        await first.close()
        assert [envelope async for envelope in client.receive()] == []
    with pytest.raises(TransportClosedError):
        await client.send(make_envelope())

    # It comes back on the same port: the caller dials afresh and would then replay its outbox.
    second = await _started(plain_codec, port=port)
    try:
        assert second.port == port
        async with asyncio.timeout(WAIT_S):
            await client.connect()
        await _round_trip(client, second, make_envelope())
        await client.close()
    finally:
        await second.close()
