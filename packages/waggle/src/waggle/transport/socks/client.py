"""Open a TCP connection through a loopback SOCKS proxy that resolves the destination by name.

A Night Veil Cell reaches the Hive Stand only at the Hive Stand's Tor onion service (ADR-0030,
codingrules 8.7), and a `.onion` name exists only inside Tor: it is never looked up on the Cell,
where a DNS query for it would leak it to whoever answers (RFC 7686). The Cell's Waggle transport
therefore hands the name to Tor's own SOCKS port, which builds the circuit and connects, and this
module is that one exchange: RFC 1928 SOCKS5 with no authentication and the destination as a
domain name (`socks5h`, the `h` for "the proxy resolves the host"), or SOCKS4a (the same idea in
the older protocol, which Tor also speaks). It runs on asyncio's non-blocking socket calls rather
than streams because the connected socket is handed on to the WebSocket client (`sock=`), which
must own it, and every read takes exactly the bytes the reply holds, so nothing that arrives
after the proxy's answer is ever consumed here. The proxy must be on this machine's loopback: a
proxy anywhere else would see the destination's name in the clear and could answer for it.
Connecting to the proxy and waiting for its answer are each bounded by a named timeout, and every
failure is a `ProxyFailedError`, never a quiet fall back to a direct connection.

Fits into the Hive:
    Its own layer, inside `waggle.transport.socks`. Called by
    `waggle.transport.websocket_client` when a `DialOptions` names a SOCKS proxy (a Night Veil
    Cell's Warden, `hivemind.cli.in_cell.main`). Calls into `waggle.errors`, `waggle.uris` and
    the standard library only.

Key invariants:
    - The destination host is always sent to the proxy as text; this module never resolves it.
    - A `SocksProxy` names a `socks5h` or `socks4a` proxy on a loopback host, with no credentials.
    - On success exactly the proxy's reply has been read; on any failure the socket is closed.

See Also:
    - RFC 1928 for SOCKS5; the SOCKS4a amendment for the 4a form; Tor's socks-extensions for the
      onion-service reply codes.
    - waggle.transport.socks.fake for FakeSocksProxy, the in-process proxy tests dial.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from waggle.errors import ProxyFailedError
from waggle.uris import is_loopback_host

SOCKS5H_SCHEME = "socks5h"  # SOCKS5, the destination named and resolved by the proxy.
SOCKS4A_SCHEME = "socks4a"  # SOCKS4 with the 4a name extension: resolved by the proxy too.
SOCKS_SCHEMES = frozenset({SOCKS5H_SCHEME, SOCKS4A_SCHEME})
PROXY_CONNECT_TIMEOUT_S = 10.0  # TCP connect to a proxy on loopback: instant unless it is down.
PROXY_REPLY_TIMEOUT_S = 120.0  # Tor's own SocksTimeout: the longest it takes to build a circuit.
MAX_NAME_BYTES = 255  # RFC 1928: the name's length travels in one byte.

_SOCKS5 = 5
_SOCKS4 = 4
_NO_AUTHENTICATION = 0x00
_CONNECT = 0x01
_RESERVED = 0x00
_ADDRESS_LENGTHS = {0x01: 4, 0x04: 16}  # IPv4 and IPv6 bound addresses; 0x03 is a name.
_DOMAIN_NAME = 0x03
_SUCCEEDED = 0x00
_PORT_BYTES = 2
_SOCKS4_GRANTED = 0x5A
_SOCKS4A_NAME_FOLLOWS = bytes((0, 0, 0, 1))  # SOCKS4a: an address of 0.0.0.x means "a name".
_SOCKS4_REPLY_BYTES = 8
_NUL = b"\x00"

# Why a proxy refused, by reply code: RFC 1928's own, then Tor's for onion services.
_SOCKS5_REFUSALS = {
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
    0xF0: "onion service descriptor not found",
    0xF1: "onion service descriptor is invalid",
    0xF2: "onion service introduction failed",
    0xF3: "onion service rendezvous failed",
    0xF4: "onion service needs client authorization",
    0xF5: "onion service client authorization was refused",
    0xF6: "onion address is invalid",
    0xF7: "onion service introduction timed out",
}
_SOCKS4_REFUSALS = {
    0x5B: "request rejected or failed",
    0x5C: "the proxy could not reach this client's identd",
    0x5D: "this client's identd reported a different user",
}

__all__ = [
    "DEFAULT_SOCKS_TIMEOUTS",
    "MAX_NAME_BYTES",
    "PROXY_CONNECT_TIMEOUT_S",
    "PROXY_REPLY_TIMEOUT_S",
    "SOCKS4A_SCHEME",
    "SOCKS5H_SCHEME",
    "SOCKS_SCHEMES",
    "SocksProxy",
    "SocksTimeouts",
    "open_socks_connection",
]


@dataclass(frozen=True, slots=True)
class SocksProxy:
    """A SOCKS proxy on this machine's loopback that resolves the destination's name itself.

    Attributes:
        scheme: `socks5h` or `socks4a`.
        host: The proxy's loopback host: `localhost` or a loopback literal.
        port: The proxy's port.
    """

    scheme: str
    host: str
    port: int

    @classmethod
    def parse(cls, url: str) -> SocksProxy:
        """Parse `socks5h://127.0.0.1:9050` (or `socks4a://`) into a SocksProxy.

        Args:
            url: The proxy URL, as `HIVEMIND_SOCKS_PROXY_URL` names it.

        Returns:
            The proxy it names.

        Raises:
            ValueError: The scheme resolves names locally or is not SOCKS, the host is not on
                loopback, the host or port is missing, or the URL carries credentials.
        """
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
        except ValueError as exc:
            raise ValueError(f"SOCKS proxy URL {url!r} cannot be parsed.") from exc
        scheme = parts.scheme.lower()
        # socks5 or socks4 would have this side resolve the name: a leak for an onion service.
        if scheme not in SOCKS_SCHEMES:
            raise ValueError(
                f"SOCKS proxy URL {url!r} must use socks5h:// or socks4a://, the schemes that "
                "have the proxy resolve the destination's name."
            )
        if host is None or port is None or parts.username is not None:
            raise ValueError(f"SOCKS proxy URL {url!r} must name a host and a port, and no user.")
        if not is_loopback_host(host):
            raise ValueError(
                f"SOCKS proxy URL {url!r} must be on this machine's loopback: a proxy elsewhere "
                "would see the destination's name in the clear."
            )
        return cls(scheme=scheme, host=host, port=port)

    def __str__(self) -> str:
        """Render back to the URL form, for a message."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


@dataclass(frozen=True, slots=True)
class SocksTimeouts:
    """How long each step through the proxy may take, in real seconds.

    Attributes:
        connect_s: The TCP connect to the proxy itself.
        reply_s: The whole SOCKS exchange, the proxy's own connection to the destination included.
    """

    connect_s: float = PROXY_CONNECT_TIMEOUT_S
    reply_s: float = PROXY_REPLY_TIMEOUT_S


DEFAULT_SOCKS_TIMEOUTS = SocksTimeouts()  # One frozen instance; every default dial's timeouts.


async def open_socks_connection(
    proxy: SocksProxy, host: str, port: int, timeouts: SocksTimeouts = DEFAULT_SOCKS_TIMEOUTS
) -> socket.socket:
    """Connect to `host`:`port` through `proxy`, and return the connected, non-blocking socket.

    Args:
        proxy: The loopback SOCKS proxy to connect through.
        host: The destination's name (or literal), sent to the proxy as text, never resolved.
        port: The destination's port.
        timeouts: The bound on reaching the proxy, and on its whole answer.

    Returns:
        A socket whose next byte is the destination's, ready for the WebSocket handshake.

    Raises:
        ProxyFailedError: The proxy could not be reached, refused, closed early, spoke something
            other than SOCKS, or did not answer within `timeouts.reply_s`.
        ValueError: `host` is longer than a SOCKS name may be.
    """
    name = _encoded_name(host)
    loop = asyncio.get_running_loop()
    sock = await _connect_to_proxy(loop, proxy, timeouts.connect_s)
    try:
        # One bound over the whole exchange: Tor answers only once its circuit is built.
        async with asyncio.timeout(timeouts.reply_s):
            if proxy.scheme == SOCKS5H_SCHEME:
                await _socks5_connect(loop, sock, name, port)
            else:
                await _socks4a_connect(loop, sock, name, port)
    except TimeoutError as exc:
        sock.close()
        raise ProxyFailedError(
            f"The SOCKS proxy {proxy} did not open {host}:{port} within {timeouts.reply_s} s."
        ) from exc
    except BaseException:
        sock.close()  # A half-negotiated socket is never handed on.
        raise
    return sock


async def _connect_to_proxy(
    loop: asyncio.AbstractEventLoop, proxy: SocksProxy, timeout_s: float
) -> socket.socket:
    """Open a TCP connection to the proxy itself, trying each loopback address it has."""
    # A loopback literal or `localhost`: answered from this machine alone, never by a resolver.
    infos = await loop.getaddrinfo(proxy.host, proxy.port, type=socket.SOCK_STREAM)
    failure: OSError | None = None
    for family, kind, protocol, _name, address in infos:
        sock = socket.socket(family, kind, protocol)
        sock.setblocking(False)
        try:
            async with asyncio.timeout(timeout_s):
                await loop.sock_connect(sock, address)
        except OSError as exc:  # Refused, unreachable, or the timeout (a TimeoutError).
            sock.close()
            failure = exc
            continue
        except BaseException:
            sock.close()
            raise
        return sock
    raise ProxyFailedError(f"Could not reach the SOCKS proxy {proxy}: {failure}.") from failure


async def _socks5_connect(
    loop: asyncio.AbstractEventLoop, sock: socket.socket, name: bytes, port: int
) -> None:
    """Run RFC 1928's greeting and CONNECT, the destination given as a domain name."""
    await loop.sock_sendall(sock, bytes((_SOCKS5, 1, _NO_AUTHENTICATION)))
    version, method = await _recv_exactly(loop, sock, 2)
    _expect_version(version, _SOCKS5)
    if method != _NO_AUTHENTICATION:
        raise ProxyFailedError("The SOCKS proxy requires authentication; a Waggle link has none.")
    header = bytes((_SOCKS5, _CONNECT, _RESERVED, _DOMAIN_NAME, len(name)))
    await loop.sock_sendall(sock, header + name + port.to_bytes(_PORT_BYTES, "big"))
    version, reply, _reserved, address_type = await _recv_exactly(loop, sock, 4)
    _expect_version(version, _SOCKS5)
    if reply != _SUCCEEDED:
        why = _SOCKS5_REFUSALS.get(reply, f"reply code {reply:#04x}")
        raise ProxyFailedError(f"The SOCKS proxy refused the connection: {why}.")
    # The bound address and port end the reply; read them so the tunnel starts clean.
    await _recv_exactly(loop, sock, await _bound_address_length(loop, sock, address_type))
    await _recv_exactly(loop, sock, _PORT_BYTES)


async def _socks4a_connect(
    loop: asyncio.AbstractEventLoop, sock: socket.socket, name: bytes, port: int
) -> None:
    """Run SOCKS4a's CONNECT: no user id, the destination's name after the marker address."""
    request = bytes((_SOCKS4, _CONNECT)) + port.to_bytes(_PORT_BYTES, "big")
    await loop.sock_sendall(sock, request + _SOCKS4A_NAME_FOLLOWS + _NUL + name + _NUL)
    reply = await _recv_exactly(loop, sock, _SOCKS4_REPLY_BYTES)
    _expect_version(reply[0], 0)  # A SOCKS4 reply's version byte is always zero.
    if reply[1] != _SOCKS4_GRANTED:
        why = _SOCKS4_REFUSALS.get(reply[1], f"reply code {reply[1]:#04x}")
        raise ProxyFailedError(f"The SOCKS proxy refused the connection: {why}.")


async def _bound_address_length(
    loop: asyncio.AbstractEventLoop, sock: socket.socket, address_type: int
) -> int:
    """Return how many bytes the reply's bound address holds, reading a name's length byte."""
    if address_type == _DOMAIN_NAME:
        (length,) = await _recv_exactly(loop, sock, 1)
        return length
    if address_type not in _ADDRESS_LENGTHS:
        raise ProxyFailedError(f"The SOCKS proxy answered with address type {address_type:#04x}.")
    return _ADDRESS_LENGTHS[address_type]


async def _recv_exactly(loop: asyncio.AbstractEventLoop, sock: socket.socket, count: int) -> bytes:
    """Read exactly `count` bytes, or raise when the proxy closes first."""
    received = bytearray()
    while len(received) < count:
        chunk = await loop.sock_recv(sock, count - len(received))
        if not chunk:
            raise ProxyFailedError("The SOCKS proxy closed the connection before it answered.")
        received += chunk
    return bytes(received)


def _expect_version(version: int, expected: int) -> None:
    """Refuse a reply that is not the SOCKS version the request spoke."""
    if version != expected:
        raise ProxyFailedError(
            f"The proxy answered as SOCKS version {version}, not {expected}: it is not a SOCKS "
            "proxy, or not the one configured."
        )


def _encoded_name(host: str) -> bytes:
    """Return `host` as the bytes a SOCKS request carries, refusing one that cannot fit."""
    try:
        name = host.encode("idna")
    except UnicodeError as exc:  # An empty or over-long label: no proxy could be asked for it.
        raise ValueError(f"Host {host!r} cannot be named to a SOCKS proxy: {exc}.") from exc
    if not name or len(name) > MAX_NAME_BYTES:
        raise ValueError(f"Host {host!r} cannot be named to a SOCKS proxy (1 to 255 bytes).")
    return name
