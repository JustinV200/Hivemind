"""Provide SystemInterfaces, the LocalInterfaces that asks the operating system through psutil.

The standard library cannot list a host's interface addresses portably: ``socket.if_nameindex``
gives names without addresses and does not exist on every Windows build, and ``getaddrinfo`` on
the host name answers for whatever the resolver says rather than for what is actually assigned.
``psutil.net_if_addrs`` asks the kernel on Linux, Windows and macOS alike, which is why it is a
dependency (roadmap 10.5a). The call is quick but blocking, so it runs in a worker thread; the
answer is converted by ``interfaces_from_records``, a pure function a test can feed directly,
keeping only IPv4 and IPv6 addresses (link-layer entries are skipped) and dropping IPv6 zones.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.interfaces``.
    Implements ``LocalInterfaces``; built by the Entrance's composition root and read by
    ``hivemind.entrance.expose.gather``. Calls into ``psutil`` and the standard library.

Key invariants:
    - Only ``AF_INET`` and ``AF_INET6`` records become addresses; everything else is ignored.
    - An address the kernel reports that ``ipaddress`` cannot parse is skipped and logged at
      debug with the interface name, never guessed at.

See Also:
    - hivemind.entrance.expose.interfaces.protocol for the protocol and the snapshot value.
    - hivemind.entrance.expose.interfaces.fake for the table-driven fake.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping, Sequence
from typing import Protocol

import psutil

from hivemind.common.logging import get_logger
from hivemind.entrance.expose.interfaces.protocol import InterfaceAddresses, IPAddress, unscoped

# The two address families an Entrance listener can bind; psutil also reports link-layer ones
# (AF_PACKET on Linux, AF_LINK elsewhere), which carry a MAC address, not an IP address.
_IP_FAMILIES = frozenset({socket.AF_INET, socket.AF_INET6})

log = get_logger(__name__)

__all__ = ["AddressRecord", "SystemInterfaces", "interfaces_from_records"]


class AddressRecord(Protocol):
    """The two fields of psutil's ``snicaddr`` this module reads (a test can pass its own)."""

    @property
    def family(self) -> int:
        """The record's address family, e.g. ``socket.AF_INET``."""
        ...

    @property
    def address(self) -> str:
        """The address as text; an IPv6 one may carry a ``%zone``."""
        ...


class SystemInterfaces:
    """A LocalInterfaces over the running host's kernel, through ``psutil.net_if_addrs``."""

    async def snapshot(self) -> tuple[InterfaceAddresses, ...]:
        """Return every interface of this host with its IP addresses.

        Returns:
            One entry per interface psutil reports, zones dropped, link-layer entries skipped.
        """
        # Latency: one getifaddrs (or GetAdaptersAddresses) call, well under a millisecond; it
        # blocks, so it runs off the event loop. There is no timeout because a thread cannot be
        # cancelled, and a kernel that hangs here hangs the whole host anyway.
        records = await asyncio.to_thread(psutil.net_if_addrs)
        return interfaces_from_records(records)


def interfaces_from_records(
    records: Mapping[str, Sequence[AddressRecord]],
) -> tuple[InterfaceAddresses, ...]:
    """Convert psutil's per-interface records into snapshot entries.

    Args:
        records: ``psutil.net_if_addrs()``'s answer, or a test's equivalent.

    Returns:
        One ``InterfaceAddresses`` per interface, keeping only parseable IPv4 and IPv6 addresses.
    """
    return tuple(
        InterfaceAddresses(name=name, addresses=_ip_addresses(name, entries))
        for name, entries in records.items()
    )


def _ip_addresses(name: str, entries: Sequence[AddressRecord]) -> frozenset[IPAddress]:
    """Parse one interface's IP records, skipping link-layer ones and anything unparseable."""
    addresses: set[IPAddress] = set()
    # Each record is one address of one family; only IP families can be a listener's address.
    for entry in entries:
        if entry.family not in _IP_FAMILIES:
            continue
        try:
            addresses.add(unscoped(ipaddress.ip_address(entry.address)))
        except ValueError:
            # Seen on some platforms for tunnel interfaces with odd point-to-point records; the
            # address is unusable as a bind address either way, so it is skipped, not guessed.
            log.debug("entrance.interface_address_unparseable", interface=name)
    return frozenset(addresses)
