"""Define LocalInterfaces, the seam that lists this host's network interfaces and their addresses.

Two exposure modes are judged against the host's own network interfaces (ADR-0033): ``vpn`` binds
the remote listener only to an address assigned to the overlay's interface (``tailscale0``, say),
and ``lan`` only to an address assigned to some interface of this host. Reading interfaces is an
operating-system call that differs between Linux, Windows and macOS, so it is one small protocol
here with a snapshot value the pure mode check reads, a production adapter over ``psutil``
(``system``) and a fake a test fills from a table (``fake``). Addresses are compared without an
IPv6 zone (``fe80::1%eth0`` and ``fe80::1`` are the same address on the interface that owns it),
so ``unscoped`` lives here too, the one place that rule is written.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.interfaces``.
    Implemented by ``SystemInterfaces`` and ``FakeInterfaces``; read by
    ``hivemind.entrance.expose.gather`` into the facts the mode check decides on. Imports the
    standard library only.

Key invariants:
    - A snapshot lists every interface at most once, and no address in it carries an IPv6 zone.
    - Implementations never raise for an interface that has no IP address; they report it with
      an empty address set.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the vpn and lan
      rules.
    - hivemind.entrance.expose.plan for the check that reads a snapshot.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Protocol

_ZONE_SEPARATOR = "%"  # An IPv6 literal's zone id ("fe80::1%eth0"), dropped by unscoped().

__all__ = ["IPAddress", "InterfaceAddresses", "LocalInterfaces", "unscoped"]

# Either family of address; ipaddress has no common base class that mypy can narrow on.
type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class InterfaceAddresses:
    """One network interface of this host and every IP address assigned to it.

    Attributes:
        name: The operating system's name for it: ``tailscale0`` or ``eth0`` on Linux,
            ``Tailscale`` or ``Ethernet`` on Windows, ``utun4`` or ``en0`` on macOS.
        addresses: Its IPv4 and IPv6 addresses, without zones; empty for an interface that is up
            with no address, or has only a link-layer one.
    """

    name: str
    addresses: frozenset[IPAddress]


class LocalInterfaces(Protocol):
    """List this host's network interfaces and their addresses, as they are right now.

    Implementations are safe to call from the event loop: a blocking operating-system call runs
    in a worker thread.
    """

    async def snapshot(self) -> tuple[InterfaceAddresses, ...]:
        """Return every interface of this host with the addresses assigned to it.

        Returns:
            One entry per interface, in no particular order; an interface with no IP address
            has an empty ``addresses``.
        """
        ...


def unscoped(address: IPAddress) -> IPAddress:
    """Return ``address`` without its IPv6 zone, so two spellings of one address compare equal.

    Args:
        address: An IPv4 address (returned as is) or an IPv6 one, zoned or not.

    Returns:
        The same address with no ``%zone``; ``ipaddress`` otherwise treats ``fe80::1%eth0`` and
        ``fe80::1`` as different addresses.
    """
    # An IPv4 address and an unzoned IPv6 one are already in their canonical form.
    if isinstance(address, ipaddress.IPv4Address) or address.scope_id is None:
        return address
    return ipaddress.IPv6Address(str(address).split(_ZONE_SEPARATOR, 1)[0])
