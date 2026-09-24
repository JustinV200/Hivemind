"""Refuse every push destination that could reach inside the Hive, and pin the one that passes.

Webhook URLs and Web Push endpoints are chosen by devices, and the Hive posts to them from inside
the operator's network (ADR-0034), so a destination is a server-side request forgery waiting to
happen: ``https://localhost:8710/`` would reach the loopback listener where approval lives, and
``http://169.254.169.254/`` a cloud metadata service. The guard allows a URL only when it is
``https``, or every address it resolves to is inside ``[entrance] vpn_cidrs`` (the overlay
encrypts), or its host or every address it resolves to is in ``[entrance.push] webhook_allowlist``;
and it never allows one when any address it resolves to is loopback, link-local, unspecified,
multicast, an IPv4-mapped IPv6 form of those, or one of the Hive Stand's own addresses, whatever
the allowlist says. The rules are pure (``parse_destination``, ``check_addresses``); name
resolution is one injected seam (``Resolver``, default ``getaddrinfo`` on the running loop), and a
name that does not resolve is refused. WHY pinning: checking a name and then letting the HTTP
client resolve it again would let a DNS answer that changes in between (rebinding) aim the request
somewhere unchecked, so a ``VettedDestination`` rebuilds the request against the one address that
was checked, with the name kept in the ``Host`` header and in TLS's SNI, so the receiver and the
certificate check still see the name.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Called by the
    registration gate (``hivemind.entrance.push.registration``) and by every channel before every
    delivery (``webhook``, ``web_push``). Calls into ``httpx`` (its URL parser, the one the client
    uses), ``ipaddress``, the injected resolver and ``hivemind.manifest.schema`` for the policy.

Key invariants:
    - A refusal is always a ``DestinationRefusedError`` carrying a ``DestinationRefusal``; no URL
      appears in a message or a log line.
    - Every address a name resolves to must pass, not only the first one.
    - A vetted request connects to exactly the address that was checked, never to a new lookup.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md, "A webhook cannot be aimed inside the
      Hive".
    - hivemind.manifest.schema.entrance.EntrancePushSection for ``webhook_allowlist``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import httpx

from hivemind.entrance.push.errors import DestinationRefusedError
from hivemind.entrance.push.models import MAX_ENDPOINT_CHARS, DeliveryOutcome
from hivemind.manifest.schema import EntranceSection

RESOLVE_TIMEOUT_S = 5.0  # A DNS answer takes milliseconds; five seconds means no answer is coming.
_DEFAULT_PORTS = {"http": 80, "https": 443}  # The only schemes a push destination may use.
_LOCALHOST = "localhost"  # RFC 6761: this name, and every name under it, is always loopback.
_ZONE_SEPARATOR = "%"  # An IPv6 literal's zone id ("fe80::1%eth0"); dropped before the checks.
# RFC 1122 "this network": is_unspecified covers only 0.0.0.0, but no receiver lives anywhere in
# 0.0.0.0/8, and some stacks send such a destination to the local host.
_THIS_NETWORK = ipaddress.ip_network("0.0.0.0/8")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address  # Either family, as ipaddress parses it.
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network  # A CIDR range of either family.

__all__ = [
    "RESOLVE_TIMEOUT_S",
    "Destination",
    "DestinationGuard",
    "DestinationPolicy",
    "DestinationRefusal",
    "IPAddress",
    "IPNetwork",
    "Resolver",
    "VettedDestination",
    "check_addresses",
    "parse_destination",
    "refusal_outcome",
    "system_resolver",
]


class DestinationRefusal(Enum):
    """Why the guard refused a destination: the one detail a refusal carries, never the URL."""

    MALFORMED = "malformed"  # Not an absolute http(s) URL with a host, or longer than allowed.
    CREDENTIALS = "credentials"  # A user name or password in the URL would sit in the tables.
    UNRESOLVABLE = "unresolvable"  # The name did not resolve, or the resolver timed out.
    LOOPBACK = "loopback"  # 127.0.0.0/8, ::1, a localhost name, or an IPv4-mapped form.
    LINK_LOCAL = "link_local"  # 169.254.0.0/16 (cloud metadata lives here), fe80::/10, mapped.
    UNSPECIFIED = "unspecified"  # 0.0.0.0/8 or ::, which can reach the local host.
    MULTICAST = "multicast"  # 224.0.0.0/4 or ff00::/8: never one receiver.
    HIVE_STAND = "hive_stand"  # One of the Hive Stand's own addresses.
    INSECURE = "insecure"  # Plain http outside the VPN ranges and the webhook allowlist.


class Resolver(Protocol):
    """Resolve a host name to its addresses: the guard's one seam to the network."""

    async def __call__(self, host: str, port: int) -> Sequence[IPAddress]:
        """Return every address ``host`` resolves to for a TCP connection to ``port``.

        Args:
            host: A lowercase ASCII (IDNA) host name, never an IP literal.
            port: The port the request will use.

        Returns:
            The addresses, in the order a connection would try them; empty if there are none.

        Raises:
            OSError: The name does not resolve (``socket.gaierror`` is an OSError).
        """
        ...


@dataclass(frozen=True, slots=True)
class DestinationPolicy:
    """What the guard allows beyond https, and the Hive Stand's addresses it always refuses.

    Attributes:
        vpn_networks: ``[entrance] vpn_cidrs``: plain http is allowed inside them.
        allowed_hosts: The host names in ``webhook_allowlist``, lowercase, no trailing dot.
        allowed_networks: The CIDR ranges (and bare addresses) in ``webhook_allowlist``.
        own_addresses: The Hive Stand's own addresses, IPv4-mapped forms reduced to IPv4.
    """

    vpn_networks: tuple[IPNetwork, ...] = ()
    allowed_hosts: frozenset[str] = frozenset()
    allowed_networks: tuple[IPNetwork, ...] = ()
    own_addresses: frozenset[IPAddress] = frozenset()

    @classmethod
    def from_manifest(
        cls, entrance: EntranceSection, own_addresses: Iterable[IPAddress]
    ) -> DestinationPolicy:
        """Build the policy from ``[entrance]`` and the addresses the Hive Stand answers on.

        Args:
            entrance: The manifest's ``[entrance]`` section (its ``vpn_cidrs`` and
                ``push.webhook_allowlist``).
            own_addresses: Every address the Hive Stand listens on or is reachable at (its
                interfaces, the remote listener, the overlay address).

        Returns:
            The policy; allowlist entries that parse as an address or a CIDR are networks, and
            every other entry is a host name.
        """
        hosts: set[str] = set()
        networks: list[IPNetwork] = []
        # Each allowlist entry is either a network (a bare address is a /32 or /128) or a name.
        for entry in entrance.push.webhook_allowlist:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                hosts.add(entry.lower().rstrip("."))
        return cls(
            vpn_networks=tuple(ipaddress.ip_network(cidr) for cidr in entrance.vpn_cidrs),
            allowed_hosts=frozenset(hosts),
            allowed_networks=tuple(networks),
            own_addresses=frozenset(_plain(address) for address in own_addresses),
        )


@dataclass(frozen=True, slots=True)
class Destination:
    """A parsed destination URL: what the guard checks and what a pinned request is rebuilt from.

    Attributes:
        scheme: ``http`` or ``https``.
        host: The host, lowercase ASCII (IDNA) without a trailing dot, or an IP literal.
        port: The explicit port, or the scheme's default.
        netloc: ``host[:port]`` exactly as the ``Host`` header carries it.
        target: The path and query, exactly as they will be sent.
    """

    scheme: str
    host: str
    port: int
    netloc: str
    target: bytes


@dataclass(frozen=True, slots=True)
class VettedDestination:
    """A destination the guard passed, pinned to the one address it checked.

    Attributes:
        destination: The parsed URL.
        address: The checked address every request for this delivery connects to.
    """

    destination: Destination
    address: IPAddress

    def request_url(self) -> httpx.URL:
        """Return the URL to request: the checked address in place of the name.

        Returns:
            The same scheme, port, path and query, with the host replaced by ``address``.
        """
        destination = self.destination
        default_port = _DEFAULT_PORTS[destination.scheme]
        port = None if destination.port == default_port else destination.port
        return httpx.URL(
            scheme=destination.scheme,
            host=str(self.address),
            port=port,
            raw_path=destination.target,
        )

    def headers(self) -> dict[str, str]:
        """Return the headers that keep the name the receiver expects on a pinned request.

        Returns:
            ``Host`` with the original name, and ``Connection: close``: a connection pooled under
            this address must never carry a request for a different name.
        """
        return {"Host": self.destination.netloc, "Connection": "close"}

    def extensions(self) -> dict[str, str]:
        """Return the httpx request extensions for a pinned request.

        Returns:
            ``sni_hostname`` for https, so TLS sends the name and verifies the certificate
            against it rather than against the address; nothing for http.
        """
        if self.destination.scheme == "https":
            return {"sni_hostname": self.destination.host}
        return {}


def parse_destination(url: str) -> Destination:
    """Parse a destination URL with the HTTP client's own parser, refusing what cannot be sent.

    Args:
        url: The URL a device registered.

    Returns:
        The parsed destination.

    Raises:
        DestinationRefusedError: MALFORMED for anything but an absolute http(s) URL with a host
            of at most ``MAX_ENDPOINT_CHARS`` characters; CREDENTIALS for a URL with userinfo.
    """
    # Bounded before parsing: the stored subscription holds at most this much.
    if len(url) > MAX_ENDPOINT_CHARS:
        raise DestinationRefusedError(DestinationRefusal.MALFORMED)
    # The client's parser, not a second one: two parsers that disagree are a classic bypass.
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise DestinationRefusedError(DestinationRefusal.MALFORMED) from exc
    if parsed.scheme not in _DEFAULT_PORTS or not parsed.raw_host:
        raise DestinationRefusedError(DestinationRefusal.MALFORMED)
    if parsed.userinfo:
        raise DestinationRefusedError(DestinationRefusal.CREDENTIALS)
    host = parsed.raw_host.decode("ascii").lower().rstrip(".")
    return Destination(
        scheme=parsed.scheme,
        host=host.split(_ZONE_SEPARATOR, 1)[0],
        port=parsed.port if parsed.port is not None else _DEFAULT_PORTS[parsed.scheme],
        netloc=parsed.netloc.decode("ascii"),
        target=parsed.raw_path,
    )


def check_addresses(
    destination: Destination, addresses: Sequence[IPAddress], policy: DestinationPolicy
) -> DestinationRefusal | None:
    """Decide whether a destination may be sent to, given every address its host resolves to.

    Args:
        destination: The parsed URL.
        addresses: Every address the host resolved to (the literal itself for an IP host).
        policy: The VPN ranges, the allowlist and the Hive Stand's own addresses.

    Returns:
        None when it may be sent to; otherwise the first rule that refuses it.
    """
    # No answer at all leaves nothing checked to connect to.
    if not addresses:
        return DestinationRefusal.UNRESOLVABLE
    # Every address must pass: a connection may use any of them, and one bad one is enough.
    for address in addresses:
        refusal = _forbidden(_plain(address), policy.own_addresses)
        if refusal is not None:
            return refusal
    if _allowed(destination, addresses, policy):
        return None
    return DestinationRefusal.INSECURE


def refusal_outcome(refusal: DestinationRefusedError) -> DeliveryOutcome:
    """Map a refusal met at delivery time to how that delivery ended.

    Args:
        refusal: What the guard raised just before sending.

    Returns:
        RETRY_LATER when the name did not resolve (a network failure right now, retried like
        one); REFUSED for every rule. Nothing was sent either way.
    """
    if refusal.reason is DestinationRefusal.UNRESOLVABLE:
        return DeliveryOutcome.RETRY_LATER
    return DeliveryOutcome.REFUSED


async def system_resolver(host: str, port: int) -> tuple[IPAddress, ...]:
    """Resolve ``host`` with the operating system's resolver, on the running event loop.

    Args:
        host: A host name.
        port: The port the request will use.

    Returns:
        Every distinct address, in the resolver's order.

    Raises:
        OSError: The name does not resolve.
    """
    # Latency: one DNS round trip, usually milliseconds; the guard bounds it with a timeout.
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # A dict keeps the first-seen order while dropping the duplicates getaddrinfo repeats.
    addresses: dict[IPAddress, None] = {}
    for _family, _type, _proto, _canonical_name, sockaddr in infos:
        addresses.setdefault(ipaddress.ip_address(sockaddr[0]), None)
    return tuple(addresses)


class DestinationGuard:
    """Vet a destination URL: parse it, resolve it through the seam, and apply the policy."""

    def __init__(self, policy: DestinationPolicy, resolver: Resolver = system_resolver) -> None:
        """Build the guard.

        Args:
            policy: What is allowed beyond https, and the Hive Stand's own addresses.
            resolver: Resolves host names; the system resolver unless a test injects one.
        """
        self._policy = policy
        self._resolver = resolver

    async def vet(self, url: str) -> VettedDestination:
        """Check ``url`` now and pin it to the address that passed.

        Args:
            url: The destination as the device registered it.

        Returns:
            The vetted destination, pinned to the first resolved address.

        Raises:
            DestinationRefusedError: Any rule refuses it; the error carries the reason only.
        """
        destination = parse_destination(url)
        addresses = await self._addresses(destination)
        refusal = check_addresses(destination, addresses, self._policy)
        if refusal is not None:
            raise DestinationRefusedError(refusal)
        return VettedDestination(destination=destination, address=_plain(addresses[0]))

    async def _addresses(self, destination: Destination) -> tuple[IPAddress, ...]:
        """Return the destination's addresses: the literal itself, or what the resolver says."""
        # RFC 6761: a localhost name is loopback whatever a resolver would answer.
        if destination.host == _LOCALHOST or destination.host.endswith(f".{_LOCALHOST}"):
            raise DestinationRefusedError(DestinationRefusal.LOOPBACK)
        literal = _ip_literal(destination.host)
        if literal is not None:
            return (literal,)
        try:
            # Latency: one DNS lookup; on timeout the name counts as unresolvable.
            async with asyncio.timeout(RESOLVE_TIMEOUT_S):
                return tuple(await self._resolver(destination.host, destination.port))
        except (OSError, ValueError) as exc:
            # gaierror and TimeoutError are OSErrors; ValueError is an answer ipaddress rejects.
            raise DestinationRefusedError(DestinationRefusal.UNRESOLVABLE) from exc


def _forbidden(
    address: IPAddress, own_addresses: frozenset[IPAddress]
) -> DestinationRefusal | None:
    """Return the rule that forbids ``address`` (already reduced by ``_plain``), or None."""
    if address.is_unspecified or address in _THIS_NETWORK:
        return DestinationRefusal.UNSPECIFIED
    if address.is_loopback:
        return DestinationRefusal.LOOPBACK
    if address.is_link_local:
        return DestinationRefusal.LINK_LOCAL
    if address.is_multicast:
        return DestinationRefusal.MULTICAST
    if address in own_addresses:
        return DestinationRefusal.HIVE_STAND
    return None


def _allowed(
    destination: Destination, addresses: Sequence[IPAddress], policy: DestinationPolicy
) -> bool:
    """Apply the allow rule to a destination none of whose addresses is forbidden."""
    # https authenticates the receiver and encrypts the notice wherever it goes.
    if destination.scheme == "https":
        return True
    # The operator named this host in [entrance.push] webhook_allowlist.
    if destination.host in policy.allowed_hosts:
        return True
    # Plain http only when every address is inside the overlay or an allowlisted network, so
    # whichever address the request is pinned to, the notice never crosses the open internet.
    trusted = (*policy.vpn_networks, *policy.allowed_networks)
    return all(any(_plain(address) in network for network in trusted) for address in addresses)


def _plain(address: IPAddress) -> IPAddress:
    """Reduce an address to what the rules compare: IPv4 for an IPv4-mapped IPv6, and no zone."""
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return mapped
        # Rebuilding from the integer drops a zone id, which only link-local addresses carry.
        return ipaddress.IPv6Address(int(address))
    return address


def _ip_literal(host: str) -> IPAddress | None:
    """Return ``host`` as an address when it is an IP literal, else None (it is a name)."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None
