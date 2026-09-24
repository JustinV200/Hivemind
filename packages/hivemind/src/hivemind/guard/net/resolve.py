"""Resolve a host to the addresses a connection would use: the Guard's one seam to the network.

A capability can spell a loopback host many ways the string grammar never sees through (`127.1`,
`2130706433`, `0x7f.1`, or a DNS name whose answer is 127.0.0.1), so a tool that is about to
connect resolves the host first and asks the Guard about every address it got back
(ADR-0033: bees never reach the Hive Stand's own state). `Resolver` is that seam: one async call
from a name to its addresses. `system_resolver` is the production implementation, the operating
system's own resolver on the running event loop; `hivemind.guard.net.fake.FakeResolver` answers
from a table in tests, so nothing under test ever performs a real lookup. `resolve_host` is what a
caller uses: an IP literal is its own answer (no lookup), a name is resolved under
`RESOLVE_TIMEOUT_S`, and anything that does not come back as at least one address raises
`UnresolvableHostError` so the caller can refuse to connect rather than guess.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.net`. Called
    by `hivemind.workers.tools.http` before it asks the Guard about a destination, and by the
    composition roots that learn the Hive Stand's own addresses. Calls into `asyncio`, `socket`,
    `ipaddress`, `hivemind.guard.errors` and `.addresses`.

Key invariants:
    - Every address `resolve_host` returns is reduced by `plain_address` and appears once, in the
      resolver's order: the address a caller pins its connection to is always the first one.
    - `resolve_host` never returns an empty tuple: no answer is an `UnresolvableHostError`.
    - A lookup never outlives `RESOLVE_TIMEOUT_S`; a timeout is an `UnresolvableHostError` too.

See Also:
    - hivemind.guard.net.addresses for what the returned addresses are then checked against.
    - hivemind.guard.net.fake for FakeResolver, the resolver every test uses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Sequence
from typing import Protocol

from hivemind.guard.errors import UnresolvableHostError
from hivemind.guard.net.addresses import IPAddress, ip_literal, plain_address

RESOLVE_TIMEOUT_S = 5.0  # A DNS answer takes milliseconds; five seconds means none is coming.

__all__ = ["RESOLVE_TIMEOUT_S", "Resolver", "resolve_host", "system_resolver"]


class Resolver(Protocol):
    """Resolve a host name to its addresses for a TCP connection to one port."""

    async def __call__(self, host: str, port: int) -> Sequence[IPAddress]:
        """Return every address `host` resolves to for a connection to `port`.

        Args:
            host: A host name, never an IP literal (`resolve_host` answers those itself).
            port: The port the connection will use.

        Returns:
            The addresses, in the order a connection would try them; empty if there are none.

        Raises:
            OSError: The name does not resolve (`socket.gaierror` is an OSError).
        """
        ...


async def system_resolver(host: str, port: int) -> tuple[IPAddress, ...]:
    """Resolve `host` with the operating system's resolver, on the running event loop.

    Args:
        host: A host name.
        port: The port the connection will use.

    Returns:
        Every distinct address, in the resolver's order.

    Raises:
        OSError: The name does not resolve.
    """
    # Latency: one DNS round trip, usually milliseconds; resolve_host bounds it with a timeout.
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # A dict keeps the first-seen order while dropping the duplicates getaddrinfo repeats.
    addresses: dict[IPAddress, None] = {}
    for _family, _type, _proto, _canonical_name, sockaddr in infos:
        addresses.setdefault(ipaddress.ip_address(sockaddr[0]), None)
    return tuple(addresses)


async def resolve_host(host: str, port: int, resolver: Resolver) -> tuple[IPAddress, ...]:
    """Return every address a connection to `host` could use, literal or resolved.

    Args:
        host: The host a caller is about to connect to: a name or an IP literal.
        port: The port the connection will use.
        resolver: Resolves a name; never called for an IP literal.

    Returns:
        At least one address, each reduced by `plain_address`, deduplicated, in resolver order.

    Raises:
        UnresolvableHostError: The name did not resolve, resolved to nothing, answered with
            something that is not an address, or did not answer within `RESOLVE_TIMEOUT_S`.
    """
    # An IP literal is its own and only answer: looking it up again could only disagree.
    literal = ip_literal(host)
    if literal is not None:
        return (literal,)
    try:
        # Latency: one DNS lookup; on timeout the host counts as unresolvable.
        async with asyncio.timeout(RESOLVE_TIMEOUT_S):
            answered = await resolver(host, port)
    except (OSError, ValueError) as exc:
        # gaierror and TimeoutError are OSErrors; ValueError is an answer ipaddress rejected.
        raise UnresolvableHostError(host, type(exc).__name__) from exc
    addresses = tuple(dict.fromkeys(plain_address(address) for address in answered))
    # No answer at all leaves nothing checked to connect to.
    if not addresses:
        raise UnresolvableHostError(host, "no address")
    return addresses
