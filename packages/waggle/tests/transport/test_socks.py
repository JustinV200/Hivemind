"""Tests for waggle.transport.socks: the SOCKS5/SOCKS4a handshake against a loopback proxy.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises `open_socks_connection` against
    `FakeSocksProxy` on loopback (a success over each protocol, refusal reply codes, a proxy that
    hangs up, one that never answers, one that is not there) and against a raw server that is not
    SOCKS at all, plus `SocksProxy.parse`'s rules. Every await is bounded by `WAIT_S`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.socks.client for the module under test.
    - waggle.transport.socks.fake for the proxy it dials.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest

from waggle.errors import ProxyFailedError
from waggle.transport.socks import (
    FakeSocksBehaviour,
    FakeSocksProxy,
    SocksProxy,
    SocksTimeouts,
    open_socks_connection,
)

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
TINY_S = 0.05  # A reply timeout short enough for a stalled proxy to trip it at once.


@pytest.fixture
async def echo_port() -> AsyncIterator[int]:
    """A loopback server that echoes each connection's bytes back, standing in for a peer."""

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(await reader.read(1024))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def _proxy(
    routes: dict[str, tuple[str, int]] | None = None, behaviour: FakeSocksBehaviour | None = None
) -> FakeSocksProxy:
    """A started FakeSocksProxy."""
    proxy = FakeSocksProxy(routes, behaviour=behaviour)
    await proxy.start()
    return proxy


async def _exchange(sock: socket.socket, payload: bytes) -> bytes:
    """Send `payload` over the tunnel and return what comes back."""
    loop = asyncio.get_running_loop()
    await loop.sock_sendall(sock, payload)
    return await loop.sock_recv(sock, 1024)


@pytest.mark.parametrize("scheme", ["socks5h", "socks4a"])
async def test_a_named_destination_is_reached_through_the_proxy_never_resolved_here(
    scheme: str, echo_port: int
) -> None:
    proxy = await _proxy({ONION: ("127.0.0.1", echo_port)})
    try:
        async with asyncio.timeout(WAIT_S):
            sock = await open_socks_connection(SocksProxy.parse(proxy.url(scheme)), ONION, 8710)
            with sock:
                assert await _exchange(sock, b"buzz") == b"buzz"
    finally:
        await proxy.close()

    assert proxy.requests == [(ONION, 8710)]


@pytest.mark.parametrize(
    ("code", "why"),
    [
        (0x01, "general SOCKS server failure"),
        (0x04, "host unreachable"),
        (0x05, "connection refused"),
        (0xF0, "onion service descriptor not found"),
        (0x42, "reply code 0x42"),
    ],
)
async def test_a_socks5_refusal_names_its_reply_code(code: int, why: str) -> None:
    proxy = await _proxy(behaviour=FakeSocksBehaviour(refuse_with=code))
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ProxyFailedError, match=why):
                await open_socks_connection(SocksProxy.parse(proxy.url()), ONION, 80)
    finally:
        await proxy.close()


async def test_a_socks4a_refusal_is_refused_too() -> None:
    proxy = await _proxy(behaviour=FakeSocksBehaviour(refuse_with=0x01))
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ProxyFailedError, match="request rejected or failed"):
                await open_socks_connection(SocksProxy.parse(proxy.url("socks4a")), ONION, 80)
    finally:
        await proxy.close()


async def test_a_name_the_proxy_cannot_reach_is_host_unreachable() -> None:
    proxy = await _proxy()  # No routes at all.
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ProxyFailedError, match="host unreachable"):
                await open_socks_connection(SocksProxy.parse(proxy.url()), "elsewhere.test", 80)
    finally:
        await proxy.close()


@pytest.mark.parametrize("scheme", ["socks5h", "socks4a"])
async def test_a_proxy_that_hangs_up_mid_handshake_is_a_proxy_failure(scheme: str) -> None:
    proxy = await _proxy(behaviour=FakeSocksBehaviour(hang_up=True))
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ProxyFailedError, match="closed the connection"):
                await open_socks_connection(SocksProxy.parse(proxy.url(scheme)), ONION, 80)
    finally:
        await proxy.close()


async def test_a_proxy_that_never_answers_times_out_as_a_proxy_failure() -> None:
    proxy = await _proxy(behaviour=FakeSocksBehaviour(stall=True))
    timeouts = SocksTimeouts(connect_s=WAIT_S, reply_s=TINY_S)
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ProxyFailedError, match="did not open"):
                await open_socks_connection(SocksProxy.parse(proxy.url()), ONION, 80, timeouts)
    finally:
        await proxy.close()

    assert proxy.requests == [(ONION, 80)]  # It asked; the proxy simply never answered.


async def test_a_proxy_that_is_not_there_is_a_proxy_failure() -> None:
    with socket.socket() as probe:  # A loopback port nothing listens on.
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async with asyncio.timeout(WAIT_S):
        with pytest.raises(ProxyFailedError, match="Could not reach the SOCKS proxy"):
            await open_socks_connection(SocksProxy.parse(f"socks5h://127.0.0.1:{port}"), ONION, 80)


async def test_a_server_that_is_not_socks_is_refused() -> None:
    async def not_socks(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(3)
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(not_socks, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with asyncio.timeout(WAIT_S):
            with pytest.raises(ProxyFailedError, match="not a SOCKS proxy"):
                await open_socks_connection(
                    SocksProxy.parse(f"socks5h://127.0.0.1:{port}"), ONION, 80
                )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("host", ["a" * 300, ".".join(["a" * 60] * 5), "a..b"])
async def test_a_host_no_proxy_could_be_asked_for_is_refused_before_anything_is_sent(
    host: str,
) -> None:
    with pytest.raises(ValueError, match="cannot be named"):
        await open_socks_connection(SocksProxy.parse("socks5h://127.0.0.1:9"), host, 80)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("socks5h://127.0.0.1:9050", SocksProxy("socks5h", "127.0.0.1", 9050)),
        ("SOCKS4A://localhost:9050", SocksProxy("socks4a", "localhost", 9050)),
        ("socks5h://[::1]:9050", SocksProxy("socks5h", "::1", 9050)),
    ],
)
def test_a_loopback_proxy_that_resolves_names_parses(url: str, expected: SocksProxy) -> None:
    assert SocksProxy.parse(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "socks5://127.0.0.1:9050",  # This side would resolve the name: a leak.
        "http://127.0.0.1:8080",
        "socks5h://10.0.0.1:9050",  # Off this machine: the name would cross a network.
        "socks5h://127.0.0.1",  # No port.
        "socks5h://user:secret@127.0.0.1:9050",  # Credentials a Waggle link never offers.
        "socks5h://127.0.0.1:99999",  # Not a port.
    ],
)
def test_a_proxy_url_that_could_leak_or_is_malformed_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="SOCKS proxy URL"):
        SocksProxy.parse(url)


def test_a_proxy_renders_back_to_its_url() -> None:
    assert str(SocksProxy.parse("socks5h://[::1]:9050")) == "socks5h://[::1]:9050"
