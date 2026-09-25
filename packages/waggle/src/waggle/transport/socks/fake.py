"""Provide FakeSocksProxy: a loopback SOCKS5/SOCKS4a proxy that relays to routes it was given.

Tor's SOCKS port is what a Night Veil Cell's Waggle transport dials (`waggle.transport.socks.
client`), and a test cannot run Tor. This fake plays its part honestly on loopback: it speaks
RFC 1928 SOCKS5 (no authentication, CONNECT, the destination as a name or an address) and SOCKS4a,
records every destination a client named, and connects each named destination to a real
`(host, port)` from its routes (a `.onion` name standing for the Hive Stand's own listener, say),
then relays bytes both ways until either side closes. A `FakeSocksBehaviour` makes it misbehave
the ways a real proxy can: refuse every CONNECT with a given reply code, hang up mid-handshake,
or never answer at all. A name with no route is refused as "host unreachable", so a test sees
exactly which names a client asked for and that nothing reached anything else.

Fits into the Hive:
    Its own layer, inside `waggle.transport.socks`, beside the client it fakes (codingrules 14.4:
    fakes ship beside what they stand in for). Used by waggle's transport tests and by
    hivemind's in-Cell tests. Calls into the standard library only.

Key invariants:
    - It binds loopback only, on an OS-assigned port, and connects only to its routes.
    - Every connection handler it starts is tracked, and `close` cancels and awaits each one
      before it returns: no task outlives the proxy (codingrules 11).

See Also:
    - waggle.transport.socks.client for the handshake this answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass

_LOOPBACK = "127.0.0.1"  # Where the fake listens, and the only interface it binds.
_SOCKS5 = 5
_SOCKS4 = 4
_HOST_UNREACHABLE = 0x04  # SOCKS5's reply for a name with no route.
_SOCKS4_GRANTED = 0x5A
_SOCKS4_REFUSED = 0x5B
_SUCCESS_REPLY = bytes((_SOCKS5, 0, 0, 0x01, 0, 0, 0, 0, 0, 0))  # Bound to 0.0.0.0:0.
_CHUNK_BYTES = 65_536  # One relay read; any size works, this one keeps a frame in few reads.

__all__ = ["FakeSocksBehaviour", "FakeSocksProxy"]


@dataclass(frozen=True, slots=True)
class FakeSocksBehaviour:
    """How the fake misbehaves, if at all.

    Attributes:
        refuse_with: A SOCKS5 reply code to answer every CONNECT with instead of connecting
            (a SOCKS4a request is refused with its one refusal code); None connects.
        hang_up: Close the connection once the client's first message is read, answering nothing.
        stall: Read the request, then never answer, until the proxy is closed.
    """

    refuse_with: int | None = None
    hang_up: bool = False
    stall: bool = False


class FakeSocksProxy:
    """A loopback SOCKS proxy that relays each named destination to one of its routes."""

    def __init__(
        self,
        routes: Mapping[str, tuple[str, int]] | None = None,
        *,
        behaviour: FakeSocksBehaviour | None = None,
    ) -> None:
        """Create the proxy; nothing listens until `start`.

        Args:
            routes: Destination name (or address text) to the real `(host, port)` it reaches.
            behaviour: How to misbehave; a well-behaved proxy when omitted.
        """
        self.routes: dict[str, tuple[str, int]] = dict(routes or {})
        self.requests: list[tuple[str, int]] = []  # Every destination named, in order.
        self._behaviour = behaviour if behaviour is not None else FakeSocksBehaviour()
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> int:
        """The port the proxy listens on; only once started."""
        if self._server is None:
            raise RuntimeError("FakeSocksProxy.port is known only once start() has run.")
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    def url(self, scheme: str = "socks5h") -> str:
        """Return this proxy's URL under `scheme` (`socks5h` or `socks4a`)."""
        return f"{scheme}://{_LOOPBACK}:{self.port}"

    async def start(self) -> None:
        """Listen on loopback, on an OS-assigned port."""
        self._server = await asyncio.start_server(self._handle, _LOOPBACK, 0)

    async def close(self) -> None:
        """Stop listening, and cancel and await every connection handler still running."""
        if self._server is not None:
            self._server.close()
        for task in tuple(self._handlers):
            task.cancel()
        await asyncio.gather(*self._handlers, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client: its SOCKS request, then the relay; track this task until it ends."""
        task = asyncio.current_task()
        assert task is not None  # noqa: S101 - always inside the task start_server made for it.
        self._handlers.add(task)
        try:
            with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
                await self._serve(reader, writer)
        finally:
            writer.close()
            self._handlers.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer one request per the behaviour; relay when it connects."""
        version = (await reader.readexactly(1))[0]
        if version not in (_SOCKS5, _SOCKS4) or self._behaviour.hang_up:
            return  # Not SOCKS, or told to hang up before answering anything.
        socks5 = version == _SOCKS5
        destination = await (_read_socks5(reader, writer) if socks5 else _read_socks4a(reader))
        self.requests.append(destination)
        if self._behaviour.stall:
            await asyncio.Event().wait()  # Never answers; close() cancels this handler.
        route = self.routes.get(destination[0])
        if self._behaviour.refuse_with is not None or route is None:
            code = self._behaviour.refuse_with or _HOST_UNREACHABLE
            writer.write(_refusal(socks5, code))
            return
        target_reader, target_writer = await asyncio.open_connection(*route)
        writer.write(_SUCCESS_REPLY if socks5 else bytes((0, _SOCKS4_GRANTED)) + bytes(6))
        await _relay((reader, writer), (target_reader, target_writer))


async def _read_socks5(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> tuple[str, int]:
    """Read a SOCKS5 greeting (choose no authentication) and CONNECT; return its destination."""
    (method_count,) = await reader.readexactly(1)
    await reader.readexactly(method_count)
    writer.write(bytes((_SOCKS5, 0)))
    _version, _command, _reserved, address_type = await reader.readexactly(4)
    if address_type == 0x03:
        (length,) = await reader.readexactly(1)
        host = (await reader.readexactly(length)).decode("idna")
    else:
        size = 4 if address_type == 0x01 else 16
        host = str(ipaddress.ip_address(await reader.readexactly(size)))
    return host, int.from_bytes(await reader.readexactly(2), "big")


async def _read_socks4a(reader: asyncio.StreamReader) -> tuple[str, int]:
    """Read the rest of a SOCKS4a CONNECT; return its destination (the name after the id)."""
    _command, port_high, port_low = await reader.readexactly(3)
    address = await reader.readexactly(4)
    await reader.readuntil(b"\x00")  # The user id, empty from a Waggle client.
    if address[:3] == bytes(3) and address[3] != 0:
        host = (await reader.readuntil(b"\x00"))[:-1].decode("idna")
    else:
        host = str(ipaddress.IPv4Address(address))
    return host, (port_high << 8) | port_low


def _refusal(socks5: bool, code: int) -> bytes:
    """Build the reply refusing a CONNECT with `code` (SOCKS4a has one refusal code)."""
    if socks5:
        return bytes((_SOCKS5, code, 0, 0x01, 0, 0, 0, 0, 0, 0))
    return bytes((0, _SOCKS4_REFUSED)) + bytes(6)


async def _relay(
    client: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    target: tuple[asyncio.StreamReader, asyncio.StreamWriter],
) -> None:
    """Copy bytes both ways until both directions have ended, or one side resets."""
    try:
        # A task group, so a reset on one side cancels the other copy rather than orphaning it.
        async with asyncio.TaskGroup() as group:
            group.create_task(_pump(client[0], target[1]))
            group.create_task(_pump(target[0], client[1]))
    except* OSError:
        pass  # A side reset the connection: the relay is over either way.
    finally:
        target[1].close()


async def _pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    """Copy `source` into `sink` until `source` ends, then close `sink`."""
    try:
        while chunk := await source.read(_CHUNK_BYTES):
            sink.write(chunk)
            await sink.drain()
    finally:
        sink.close()
