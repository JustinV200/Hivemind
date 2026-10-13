"""Tests for wss:// listeners and pinned dials over real loopback TLS connections.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Starts a WebSocketServer with a generated
    self-signed certificate (``server_ssl_context``) and dials it with a WebSocketClientTransport:
    the wss:// URI, a pinned dial that connects and exchanges frames both ways, a wrong pin and a
    different valid certificate each raising TlsPinMismatchError at once with no retry and the
    link aborted before a frame is sent, a wss:// dial without a pin refusing a self-signed
    certificate, a pin refused on a plaintext URI, the pin's spelling checked, and a pinned dial
    through a SOCKS proxy.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.tls, waggle.transport.websocket_client and waggle.transport.websocket_server
      for the code under test.
    - conftest.py for the generated certificate fixtures.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

import pytest
from websockets.asyncio.client import connect

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import (
    ConnectFailedError,
    ConnectionLostError,
    FrameCeilingError,
    TlsPinMismatchError,
)
from waggle.messages.control.protocol import Ping, Pong
from waggle.transport.ceiling import CeilingLimits
from waggle.transport.limits import ConnectionRefusal, RefusalHandler, RefusalKind, ServerLimits
from waggle.transport.socks import FakeSocksProxy
from waggle.transport.tls import (
    check_pin,
    peer_certificate_sha256,
    pinned_client_ssl_context,
    server_ssl_context,
)
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

if TYPE_CHECKING:
    # mypy resolves the conftest by its path-based module name (explicit_package_bases); the
    # annotations below are never evaluated at run time (from __future__ import annotations).
    from packages.waggle.tests.transport.conftest import TlsMaterial

MakeEnvelope = Callable[..., Envelope]

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
WRONG_PIN = "0" * 64  # Well-formed, and the fingerprint of no certificate.
ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
MAX_ATTEMPTS = 5  # Several, so a retry of a pin mismatch would show up as a backoff sleep.


class SleepCounter(FakeClock):
    """A FakeClock that records every sleep: a retry's backoff is a sleep."""

    def __init__(self) -> None:
        """Create a counter with no sleeps recorded yet."""
        super().__init__()
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """Record ``seconds``, then sleep as a FakeClock does (until advanced)."""
        self.sleeps.append(seconds)
        await super().sleep(seconds)


async def _tls_server(codec: Codec, material: TlsMaterial) -> WebSocketServer:
    """A started loopback listener serving ``material``'s certificate over TLS."""
    context = server_ssl_context(material.cert_path, material.key_path)
    server = WebSocketServer(codec, ssl_context=context)
    await server.start()
    return server


@pytest.fixture
async def tls_server(
    plain_codec: Codec, tls_material: TlsMaterial
) -> AsyncIterator[WebSocketServer]:
    """A started TLS listener with the generated certificate, closed after the test."""
    server = await _tls_server(plain_codec, tls_material)
    yield server
    await server.close()


def _client(
    uri: str, codec: Codec, pin: str | None, clock: FakeClock, proxy_url: str | None = None
) -> WebSocketClientTransport:
    """A client for ``uri`` that dials up to MAX_ATTEMPTS times, pinned to ``pin`` if given."""
    options = DialOptions(max_attempts=MAX_ATTEMPTS, tls_pin_sha256=pin, socks_proxy_url=proxy_url)
    return WebSocketClientTransport(uri, codec, clock, options=options)


async def _accept(server: WebSocketServer) -> WebSocketTransport:
    """The next connection the server accepted."""
    async with asyncio.timeout(WAIT_S):
        listing = server.connections()
        try:
            return await anext(listing)
        finally:
            await listing.aclose()


async def _exchange(
    client: WebSocketClientTransport, server: WebSocketServer, make_envelope: MakeEnvelope
) -> None:
    """Send a Ping client -> server and its Pong back, asserting both arrive intact."""
    accepted = await _accept(server)
    ping = make_envelope(Ping())
    pong = make_envelope(Pong(received_at=ping.sent_at), correlation_id=ping.id)
    async with asyncio.timeout(WAIT_S):
        await client.send(ping)
        assert await anext(accepted.receive()) == ping
        await accepted.send(pong)
        assert await anext(client.receive()) == pong


# ──────────────────────────────────────────────────────────────────────────────
# The listener
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_listener_with_an_ssl_context_has_a_wss_uri(tls_server: WebSocketServer) -> None:
    assert tls_server.uri == f"wss://127.0.0.1:{tls_server.port}"


# ──────────────────────────────────────────────────────────────────────────────
# A pinned dial
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_client_with_the_right_pin_connects_and_frames_flow_both_ways(
    tls_server: WebSocketServer,
    plain_codec: Codec,
    tls_material: TlsMaterial,
    make_envelope: MakeEnvelope,
) -> None:
    client = _client(tls_server.uri, plain_codec, tls_material.sha256, FakeClock())
    try:
        async with asyncio.timeout(WAIT_S):
            await client.connect()
        assert client.is_connected

        await _exchange(client, tls_server, make_envelope)
    finally:
        await client.close()


async def test_a_wrong_pin_raises_at_once_without_retrying_and_aborts_the_link(
    tls_server: WebSocketServer, plain_codec: Codec, tls_material: TlsMaterial
) -> None:
    clock = SleepCounter()
    client = _client(tls_server.uri, plain_codec, WRONG_PIN, clock)

    async with asyncio.timeout(WAIT_S):
        with pytest.raises(TlsPinMismatchError) as caught:
            await client.connect()

    # A ConnectFailedError, so a caller that handles a failed dial handles this; but raised on
    # the first attempt, with no backoff: a different certificate is not a flaky link.
    assert isinstance(caught.value, ConnectFailedError)
    assert caught.value.code == "waggle.transport.tls_pin"
    assert clock.sleeps == []
    assert not client.is_connected
    # Both fingerprints and the peer's address are in the message, so an operator can see which
    # certificate answered, and from where.
    assert tls_material.sha256 in str(caught.value)
    assert WRONG_PIN in str(caught.value)
    assert f"127.0.0.1:{tls_server.port}" in str(caught.value)

    # The listener saw the dial exactly once, and the client sent it no Waggle frame before it
    # dropped the connection: the aborted link ends abnormally, delivering nothing.
    accepted = await _accept(tls_server)
    received: list[Envelope] = []
    async with asyncio.timeout(WAIT_S):
        with contextlib.suppress(ConnectionLostError):
            received = [envelope async for envelope in accepted.receive()]
    assert received == []
    await tls_server.close()
    assert [transport async for transport in tls_server.connections()] == []


async def test_a_different_certificate_is_refused_even_though_it_is_valid_tls(
    plain_codec: Codec, tls_material: TlsMaterial, other_tls_material: TlsMaterial
) -> None:
    impostor = await _tls_server(plain_codec, other_tls_material)
    client = _client(impostor.uri, plain_codec, tls_material.sha256, FakeClock())
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(TlsPinMismatchError) as caught:
                await client.connect()

        # The impostor completed a genuine TLS handshake; only the pin tells it from the listener.
        assert other_tls_material.sha256 in str(caught.value)
        assert tls_material.sha256 in str(caught.value)
    finally:
        await impostor.close()


# ──────────────────────────────────────────────────────────────────────────────
# Limits and TLS together
# ──────────────────────────────────────────────────────────────────────────────


async def test_over_tls_a_signed_replay_is_dropped_and_a_flood_closed_with_4029(
    signed_codec: Codec,
    tls_material: TlsMaterial,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    context = server_ssl_context(tls_material.cert_path, tls_material.key_path)
    limits = ServerLimits(ceiling=CeilingLimits(max_frames_per_minute=60, burst=3))
    # A clock that never advances, so no bucket refills mid-test.
    server = WebSocketServer(
        signed_codec, ssl_context=context, limits=limits, on_refusal=on_refusal, clock=FakeClock()
    )
    await server.start()
    original = make_envelope()
    first = _client(server.uri, signed_codec, tls_material.sha256, FakeClock())
    second = _client(server.uri, signed_codec, tls_material.sha256, FakeClock())
    try:
        async with asyncio.timeout(WAIT_S):
            await first.connect()
            first_end = await _accept(server)
            await first.send(original)
            assert (await anext(first_end.receive())).id == original.id

            # The same signed frame on a second pinned connection is a replay: dropped, reported.
            await second.connect()
            second_end = await _accept(server)
            await second.send(original)
            for _ in range(3):
                await second.send(make_envelope())
            received: list[Envelope] = []
            with pytest.raises(FrameCeilingError):
                async for envelope in second_end.receive():
                    received.append(envelope)

        # Two fresh frames fit the burst of three beside the replay; the third fresh one did not.
        assert len(received) == 2
        assert original.id not in [envelope.id for envelope in received]
        assert [refusal.kind for refusal in refusals] == [
            RefusalKind.REPLAY,
            RefusalKind.FRAME_CEILING,
        ]
        with pytest.raises(ConnectionLostError, match="4029"):
            async with asyncio.timeout(WAIT_S):
                _ = [envelope async for envelope in second.receive()]
    finally:
        await first.close()
        await second.close()
        await server.close()


# ──────────────────────────────────────────────────────────────────────────────
# Reading the peer's certificate
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_peer_certificate_of_a_tls_connection_is_read_as_its_fingerprint(
    tls_server: WebSocketServer, tls_material: TlsMaterial
) -> None:
    async with asyncio.timeout(WAIT_S):
        raw = await connect(tls_server.uri, ssl=pinned_client_ssl_context(), proxy=None)
    try:
        assert peer_certificate_sha256(raw) == tls_material.sha256
        await check_pin(raw, tls_material.sha256)  # the right pin returns and leaves it open
        assert raw.state.name == "OPEN"
    finally:
        await raw.close()


async def test_a_connection_that_is_not_tls_has_no_certificate_to_pin(
    plain_codec: Codec,
) -> None:
    server = WebSocketServer(plain_codec)
    await server.start()
    async with asyncio.timeout(WAIT_S):
        raw = await connect(server.uri, proxy=None)
    try:
        assert peer_certificate_sha256(raw) is None

        with pytest.raises(TlsPinMismatchError, match="no certificate") as caught:
            await check_pin(raw, WRONG_PIN)
        # The mismatch aborted it: a pin that cannot be checked is a pin that failed.
        assert raw.state.name == "CLOSED"
        assert f"127.0.0.1:{server.port}" in str(caught.value)
    finally:
        await server.close()


async def test_check_pin_names_the_peer_and_still_raises_once_the_peer_is_forgotten(
    tls_server: WebSocketServer,
) -> None:
    async with asyncio.timeout(WAIT_S):
        raw = await connect(tls_server.uri, ssl=pinned_client_ssl_context(), proxy=None)

    with pytest.raises(TlsPinMismatchError) as first:
        await check_pin(raw, WRONG_PIN)
    assert f"127.0.0.1:{tls_server.port}" in str(first.value)

    # The abort made websockets forget the peer's address; a second check on the dead
    # connection still raises, and says the address is unknown instead of failing to format it.
    with pytest.raises(TlsPinMismatchError, match="address unknown"):
        await check_pin(raw, WRONG_PIN)


# ──────────────────────────────────────────────────────────────────────────────
# What a pin is not
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_wss_dial_without_a_pin_still_verifies_against_the_authorities(
    tls_server: WebSocketServer, plain_codec: Codec
) -> None:
    client = WebSocketClientTransport(
        tls_server.uri, plain_codec, FakeClock(), options=DialOptions(max_attempts=1)
    )

    async with asyncio.timeout(WAIT_S):
        with pytest.raises(ConnectFailedError) as caught:
            await client.connect()

    # The default context trusts the system's authorities, and nothing signed this certificate:
    # an ordinary failed dial, not a pin mismatch.
    assert not isinstance(caught.value, TlsPinMismatchError)
    assert isinstance(caught.value.__cause__, ssl.SSLCertVerificationError)


def test_a_pin_on_a_plaintext_uri_is_refused_at_construction(
    plain_codec: Codec, tls_material: TlsMaterial, fake_clock: FakeClock
) -> None:
    # A pin that silently did nothing would let an operator think a plaintext link is pinned.
    with pytest.raises(ValueError, match=r"wss://"):
        _client("ws://127.0.0.1:9", plain_codec, tls_material.sha256, fake_clock)


@pytest.mark.parametrize("pin", ["", "abc", "A" * 64, "g" * 64, "0" * 63, "0" * 65])
def test_dial_options_refuse_a_pin_that_is_not_a_sha256_fingerprint(pin: str) -> None:
    with pytest.raises(ValueError, match="tls_pin_sha256"):
        DialOptions(tls_pin_sha256=pin)


def test_dial_options_accept_a_fingerprint_or_none(tls_material: TlsMaterial) -> None:
    assert DialOptions().tls_pin_sha256 is None
    assert DialOptions(tls_pin_sha256=tls_material.sha256).tls_pin_sha256 == tls_material.sha256


# ──────────────────────────────────────────────────────────────────────────────
# A pinned dial through a SOCKS proxy
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def proxy(tls_server: WebSocketServer) -> AsyncIterator[FakeSocksProxy]:
    """A fake Tor SOCKS port whose one route is a name standing for the TLS listener."""
    fake = FakeSocksProxy({ONION: ("127.0.0.1", tls_server.port)})
    await fake.start()
    yield fake
    await fake.close()


async def test_a_pinned_dial_works_through_a_socks_proxy(
    tls_server: WebSocketServer,
    proxy: FakeSocksProxy,
    plain_codec: Codec,
    tls_material: TlsMaterial,
    make_envelope: MakeEnvelope,
) -> None:
    uri = f"wss://{ONION}:{tls_server.port}"
    client = _client(uri, plain_codec, tls_material.sha256, FakeClock(), proxy.url())
    try:
        async with asyncio.timeout(WAIT_S):
            await client.connect()

        await _exchange(client, tls_server, make_envelope)
        # TLS and the upgrade both ran over the proxied socket: the proxy was asked for the name.
        assert proxy.requests == [(ONION, tls_server.port)]
    finally:
        await client.close()


async def test_a_wrong_pin_through_a_socks_proxy_is_refused_at_once_too(
    tls_server: WebSocketServer, proxy: FakeSocksProxy, plain_codec: Codec
) -> None:
    clock = SleepCounter()
    client = _client(f"wss://{ONION}:{tls_server.port}", plain_codec, WRONG_PIN, clock, proxy.url())

    async with asyncio.timeout(WAIT_S):
        with pytest.raises(TlsPinMismatchError):
            await client.connect()

    assert clock.sleeps == []
    assert proxy.requests == [(ONION, tls_server.port)]  # one dial, through the proxy, no retry
