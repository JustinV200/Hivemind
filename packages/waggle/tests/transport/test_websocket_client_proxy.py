"""Tests for waggle.transport.websocket_client through a SOCKS proxy (roadmap step 10.3a).

A Night Veil Cell's link reaches the Hive Stand only at its onion service, through the SOCKS proxy
on the Cell's own loopback: every dial goes through the proxy, the destination named to it, and a
proxy that refuses is retried through the same proxy, never bypassed for a direct connection.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/waggle/transport/websocket_client.py
    (codingrules 5.1: split by feature from test_websocket_client.py). A `FakeSocksProxy` plays
    Tor's SOCKS port on loopback and routes the onion name to a real loopback `WebSocketServer`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.socks for the proxy exchange and its fake.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectFailedError, ProxyFailedError
from waggle.transport.socks import FakeSocksBehaviour, FakeSocksProxy, SocksProxy
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

MakeEnvelope = Callable[..., Envelope]

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
ONION_PORT = 8710  # The onion service's virtual port, which the proxy maps to the listener.


async def _listener(codec: Codec) -> WebSocketServer:
    """A started loopback Waggle listener, standing in for the Hive Stand."""
    server = WebSocketServer(codec)
    await server.start()
    return server


async def _proxy(
    server: WebSocketServer, behaviour: FakeSocksBehaviour | None = None
) -> FakeSocksProxy:
    """A started fake Tor SOCKS port whose one route is the onion service to `server`."""
    proxy = FakeSocksProxy({ONION: ("127.0.0.1", server.port)}, behaviour=behaviour)
    await proxy.start()
    return proxy


async def test_a_proxied_transport_reaches_the_onion_service_only_through_the_proxy(
    plain_codec: Codec, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    server = await _listener(plain_codec)
    proxy = await _proxy(server)
    proxy_url = proxy.url()
    client = WebSocketClientTransport(
        f"ws://{ONION}:{ONION_PORT}/waggle",
        plain_codec,
        fake_clock,
        options=DialOptions(socks_proxy_url=proxy_url),
    )
    envelope = make_envelope()
    try:
        async with asyncio.timeout(WAIT_S):
            await client.connect()
            listing = server.connections()
            accepted = await anext(listing)
            await listing.aclose()
            await client.send(envelope)
            receiver = accepted.receive()
            assert await anext(receiver) == envelope
            await receiver.aclose()
            await accepted.close()
    finally:
        await client.close()
        await proxy.close()
        await server.close()

    # The one route to the listener was the proxy, asked for the onion name as written.
    assert proxy.requests == [(ONION, ONION_PORT)]
    assert client.socks_proxy == SocksProxy.parse(proxy_url)


async def test_a_refusing_proxy_is_retried_through_itself_and_never_bypassed(
    plain_codec: Codec, fake_clock: FakeClock
) -> None:
    # The listener is on loopback, directly reachable: a direct fall back would connect.
    server = await _listener(plain_codec)
    port = server.port
    proxy = await _proxy(server, FakeSocksBehaviour(refuse_with=0xF0))
    options = DialOptions(max_attempts=1, socks_proxy_url=proxy.url())
    client = WebSocketClientTransport(
        f"ws://127.0.0.1:{port}", plain_codec, fake_clock, options=options
    )
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ConnectFailedError, match="through the SOCKS proxy") as caught:
                await client.connect()
    finally:
        await proxy.close()
        await server.close()

    assert isinstance(caught.value.__cause__, ProxyFailedError)
    assert "onion service descriptor not found" in str(caught.value)
    assert proxy.requests == [("127.0.0.1", port)]
    assert not client.is_connected


def test_an_onion_service_with_no_proxy_is_refused_at_construction(
    plain_codec: Codec, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match="reachable only through Tor's SOCKS proxy"):
        WebSocketClientTransport(f"ws://{ONION}:{ONION_PORT}", plain_codec, fake_clock)


@pytest.mark.parametrize(
    "proxy_url", ["socks5://127.0.0.1:9050", "socks5h://192.0.2.7:9050", "socks5h://localhost"]
)
def test_a_proxy_that_could_leak_the_name_is_refused_at_construction(
    proxy_url: str, plain_codec: Codec, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match="SOCKS proxy URL"):
        WebSocketClientTransport(
            f"ws://{ONION}:{ONION_PORT}",
            plain_codec,
            fake_clock,
            options=DialOptions(socks_proxy_url=proxy_url),
        )


def test_a_direct_transport_names_no_proxy(plain_codec: Codec, fake_clock: FakeClock) -> None:
    assert WebSocketClientTransport("ws://127.0.0.1:9", plain_codec, fake_clock).socks_proxy is None
