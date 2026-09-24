"""Provide TailscaleEndpointSource: ask tailscaled where each overlay peer really connects from.

The travel lock works only with ``expose = "vpn"`` on Tailscale (ADR-0033), because tailscaled
knows what the overlay hides: a peer's current endpoint. Its local API answers ``GET
/localapi/v0/status`` over a Unix socket (``/var/run/tailscale/tailscaled.sock`` on Linux) with
every peer, its Tailscale addresses (``TailscaleIPs``), the endpoint it is using right now
(``CurAddr``, empty when not direct) and its DERP relay region (``Relay``).
``TailscaleEndpointSource.current_network`` finds the peer whose Tailscale address is the
request's address and answers with its current endpoint's /24 or /64, or ``derp:<region>`` when it
is relayed. The HTTP client is injected (``unix_socket_client`` builds the real one over the
socket), so tests drive the parsing with ``httpx.MockTransport``. ``tailscale_source`` refuses to
build where no real endpoints can be seen: not ``vpn`` mode, Windows (whose tailscaled speaks over
a named pipe, not a Unix socket), or no socket at the path.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.travel``. Built by
    the Entrance's composition root when ``travel_lock`` is on; called by ``TravelLock``. Calls
    into ``httpx`` (the local API), ``hivemind.entrance.auth.network`` and the manifest schema.

Key invariants:
    - Only tailscaled's own socket is ever asked; nothing leaves the Hive Stand.
    - Every failure (no answer in time, an HTTP error, a status that does not parse, no such
      peer) answers None, which the travel lock treats as a new network; nothing raises.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "travel lock".
    - hivemind.entrance.auth.travel.source for the protocol.
"""

from __future__ import annotations

import asyncio
import ipaddress
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.network import address_network, relay_network
from hivemind.entrance.errors import TravelLockUnavailableError
from hivemind.manifest import EntranceExposure, EntranceSection

TAILSCALED_SOCKET = Path("/var/run/tailscale/tailscaled.sock")  # tailscaled's default on Linux.
LOCAL_API_BASE_URL = "http://local-tailscaled.sock"  # The host name tailscaled's local API expects.
STATUS_PATH = "/localapi/v0/status"  # Every peer with its addresses, endpoint and relay.
LOCAL_API_TIMEOUT_S = 2.0  # A local socket answers in milliseconds; a login never waits longer.
_WINDOWS = "win32"  # sys.platform on Windows, where tailscaled listens on a named pipe.

log = get_logger(__name__)

__all__ = [
    "LOCAL_API_BASE_URL",
    "LOCAL_API_TIMEOUT_S",
    "STATUS_PATH",
    "TAILSCALED_SOCKET",
    "TailscaleEndpointSource",
    "tailscale_source",
    "unix_socket_client",
]


class _Peer(BaseModel):
    """The part of one tailscaled ``PeerStatus`` the travel lock reads."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    tailscale_ips: list[str] | None = Field(
        default=None, alias="TailscaleIPs", description="The peer's overlay addresses."
    )
    cur_addr: str = Field(
        default="", alias="CurAddr", description="Its current direct endpoint; empty if relayed."
    )
    relay: str = Field(default="", alias="Relay", description="Its DERP relay region code.")


class _Status(BaseModel):
    """The part of tailscaled's ``Status`` the travel lock reads: its peers."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    peers: dict[str, _Peer] | None = Field(
        default=None, alias="Peer", description="Every peer by node key; null with no peers."
    )


class TailscaleEndpointSource:
    """A PeerEndpointSource over tailscaled's local API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Wrap a client that reaches tailscaled's local API (``unix_socket_client``).

        Args:
            client: An ``httpx.AsyncClient`` whose base URL is the local API's.
        """
        self._client = client

    async def current_network(self, address: str) -> str | None:
        """Return the network the peer at ``address`` connects from; see PeerEndpointSource.

        Args:
            address: The overlay address a request arrived from.

        Returns:
            Its current endpoint's /24 or /64, ``derp:<region>`` when relayed, or None.
        """
        status = await self._status()
        if status is None or not status.peers:
            return None
        wanted = _parsed(address)
        # The peer whose overlay addresses include the request's: at most one ever matches.
        for peer in status.peers.values():
            if wanted is not None and wanted in {_parsed(ip) for ip in peer.tailscale_ips or ()}:
                return _peer_network(peer)
        return None

    async def aclose(self) -> None:
        """Close the client; the composition root calls this when the Entrance stops."""
        await self._client.aclose()

    async def _status(self) -> _Status | None:
        """Ask tailscaled for its status; None on any failure (logged, never raised)."""
        try:
            # Latency: one request over a local Unix socket, milliseconds; bounded regardless.
            async with asyncio.timeout(LOCAL_API_TIMEOUT_S):
                response = await self._client.get(STATUS_PATH)
            response.raise_for_status()
            return _Status.model_validate_json(response.content)
        except (TimeoutError, httpx.HTTPError, ValidationError) as exc:
            # Unknown is an answer: the travel lock treats it as a new network (fail closed).
            log.warning("entrance.tailscale_status_failed", error=type(exc).__name__)
            return None


def unix_socket_client(socket_path: Path = TAILSCALED_SOCKET) -> httpx.AsyncClient:
    """Build the client that reaches tailscaled's local API over its Unix socket.

    Args:
        socket_path: tailscaled's socket.

    Returns:
        A client for ``LOCAL_API_BASE_URL``; nothing is connected until the first request.
    """
    transport = httpx.AsyncHTTPTransport(uds=str(socket_path))
    return httpx.AsyncClient(
        transport=transport, base_url=LOCAL_API_BASE_URL, timeout=LOCAL_API_TIMEOUT_S
    )


def tailscale_source(
    section: EntranceSection,
    socket_path: Path = TAILSCALED_SOCKET,
    platform: str = sys.platform,
) -> TailscaleEndpointSource:
    """Build the Tailscale source, refusing wherever it could not see real endpoints.

    Args:
        section: The manifest's ``[entrance]`` section.
        socket_path: tailscaled's socket.
        platform: ``sys.platform``; a parameter so the Windows refusal can be tested anywhere.

    Returns:
        The source, over ``unix_socket_client(socket_path)``.

    Raises:
        TravelLockUnavailableError: ``expose`` is not ``vpn``, the platform is Windows, or there
            is no socket at ``socket_path``.
    """
    if section.expose is not EntranceExposure.VPN:
        raise TravelLockUnavailableError(f"expose is {section.expose.value!r}, not 'vpn'")
    if platform == _WINDOWS:
        raise TravelLockUnavailableError(
            "tailscaled on Windows answers on a named pipe, not a Unix socket"
        )
    if not socket_path.is_socket():
        raise TravelLockUnavailableError(f"there is no tailscaled socket at {socket_path}")
    return TailscaleEndpointSource(unix_socket_client(socket_path))


def _parsed(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an address (an IPv4-mapped IPv6 one as its IPv4 address); None if it is not one."""
    try:
        ip = ipaddress.ip_address(address.strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _peer_network(peer: _Peer) -> str | None:
    """Return a peer's current network: its direct endpoint's, else its relay region."""
    # CurAddr is "host:port" ("[v6]:port" for IPv6); the host is everything before the last colon.
    if peer.cur_addr:
        host, _, _ = peer.cur_addr.rpartition(":")
        return address_network(host)
    return relay_network(peer.relay) if peer.relay else None
