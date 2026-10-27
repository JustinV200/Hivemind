"""Dial the Queen and announce a Virtual Cell on each connection, through Tor for Night Veil.

A Virtual Cell's link to the Queen can drop under it: Tor can die under a Night Veil Cell's
circuit, and the Queen herself can die under any Cell's link and come back (Requeening,
ADR-0062). The link redials with one of two dialers, each a `QueenDialer`. `ProxyDialer` is a
Night Veil Cell's: one attempt, always through the SOCKS proxy the Cell's bootstrap names, with
nothing in it that could connect any other way. There is no direct address in it to fall back to
(the Cell knows only the onion name, which no resolver answers), no second route, and no option
that turns the proxy off; a proxy that is down is a failed attempt, and the link's backoff tries
the same proxy again. `DirectDialer` is every other Cell's: one attempt at the Queen's URL, the
way the Cell's first connection always dialled her. `CellAnnouncer` is what the Cell says on a
fresh connection (an `Announcer`): the `cell.ready` with its tier's checks, the capacity it
reports and a heartbeat, in the order the Queen's readiness gate reads them, exactly as the
Cell's first connection did. `AfterRedial` is the step after a connection is mended: the first
connection of a Cell's life is not a reconnection, so it records and ships nothing; every later
one records `warden.reconnected` and ships the trail segment (`hivemind.wardens.offline`).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.redial`. Built by
    `hivemind.cli.in_cell.redial.build` and handed to `ReconnectingQueenLink`. Calls into
    `hivemind.cell` (RealCellSource, CombShieldLevel), `hivemind.cli.in_cell` (the three frames,
    the config, the Propolis checks), `hivemind.cli.version`, `hivemind.wardens.offline` and
    waggle only.

Key invariants:
    - Every dial of a Night Veil Cell goes through the SOCKS proxy: `ProxyDialer` cannot be built
      without one, the transport it makes refuses an onion URI that names none, and `dialer_for`
      never gives a Night Veil Cell a `DirectDialer`.
    - A dial is one attempt, bounded by the transport's own open timeout (and its SOCKS timeouts);
      a failed one raises a `TransportError` the link's backoff retries.
    - The first frame on every connection is `cell.ready`, signed with the Cell's node key; a
      re-announcement describes the Cell as it is now (`source.cells()`).
    - The checks in `cell.ready` are its tier's on every connection: a Night Veil Cell's link
      checks, a Propolis Cell's tunnel checks read afresh, none for a Meadow Cell.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for why every Cell's link mends.
    - hivemind.wardens.offline.reconnect for the QueenDialer, Announcer and AfterReconnect
      Protocols.
    - hivemind.cli.in_cell.borrowed.dial for the device's counterpart of these three.
    - waggle.transport.websocket_client for the dial itself and why it never falls back.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel, RealCellSource
from hivemind.cli.in_cell import link as cell_link
from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.cli.in_cell.link import CellLinkDeps
from hivemind.cli.in_cell.propolis import own_tunnel_attestation
from hivemind.cli.version import collect_version_info
from hivemind.wardens.offline import AfterReconnect, QueenDialer
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.messages.cell.status import AttestationCheck
from waggle.messages.forage import CapacityTrigger
from waggle.transport.base import Transport
from waggle.transport.socks import SocksProxy
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

__all__ = ["AfterRedial", "CellAnnouncer", "DirectDialer", "ProxyDialer", "dialer_for"]


def dialer_for(config: InCellRuntimeConfig, codec: Codec, clock: Clock) -> QueenDialer:
    """Return the dialer this Cell's link redials with: through Tor for Night Veil, else direct.

    Args:
        config: This Cell's runtime config; its tier decides.
        codec: Signs with the Cell's node key and verifies with the Queen's.
        clock: Paces the transport.

    Returns:
        A `ProxyDialer` for a Night Veil Cell, a `DirectDialer` for every other Cell.

    Raises:
        ValueError: A Night Veil Cell's config names no proxy (`ProxyDialer` refuses to exist).
    """
    if config.spawn_config.comb_shield is CombShieldLevel.NIGHT_VEIL:
        return ProxyDialer(config, codec, clock)
    return DirectDialer(config, codec, clock)


class ProxyDialer:
    """Open one connection to the Queen's onion service, through the SOCKS proxy only."""

    def __init__(self, config: InCellRuntimeConfig, codec: Codec, clock: Clock) -> None:
        """Remember where to dial and through what; nothing is dialled until `dial`.

        Args:
            config: This Cell's runtime config; its Queen URL (an onion service) and the proxy.
            codec: Signs with the Cell's node key and verifies with the Queen's.
            clock: Paces the transport.

        Raises:
            ValueError: The config names no proxy: a Night Veil Cell has none to fall back from,
                and one that dialled without it would leak the onion name to a resolver.
        """
        if config.socks_proxy_url is None:
            raise ValueError("A Night Veil Cell dials the Queen only through a SOCKS proxy.")
        self._url = config.queen_waggle_url
        # One attempt per dial: the link's backoff owns the waiting between attempts, and the proxy
        # is in every attempt's options, so no attempt can leave it out.
        self._options = DialOptions(
            max_attempts=1,
            allow_virtual_cell_gateway_host=True,
            socks_proxy_url=config.socks_proxy_url,
        )
        self._codec = codec
        self._clock = clock

    async def dial(self) -> Transport:
        """Make one connection attempt through the proxy.

        Returns:
            A connected transport.

        Raises:
            waggle.errors.TransportError: The attempt failed: the proxy is down or refused, Tor
                could not reach the onion service, or the handshake was rejected.
        """
        transport = WebSocketClientTransport(
            self._url, self._codec, self._clock, options=self._options
        )
        # External await: a SOCKS exchange (Tor may take tens of seconds to build a circuit to an
        # onion service, bounded by the SOCKS timeouts), then the WebSocket upgrade, bounded by the
        # transport's own open timeout; a failure raises and is retried by the link's backoff.
        await transport.connect()
        return transport


class DirectDialer:
    """Open one connection to the Queen at her URL, as every Cell but a Night Veil one dials."""

    def __init__(self, config: InCellRuntimeConfig, codec: Codec, clock: Clock) -> None:
        """Remember where to dial; nothing is dialled until `dial`.

        Args:
            config: This Cell's runtime config; its Queen URL, and its proxy if it names one.
            codec: Signs with the Cell's node key and verifies with the Queen's.
            clock: Paces the transport.
        """
        self._url = config.queen_waggle_url
        # One attempt per dial, as `ProxyDialer`'s. The gateway carve-out matches the config's own
        # check of this URL (inside a container, loopback is the Cell, so the Queen is reached
        # through the host gateway), and a proxy the bootstrap names is kept, as the Cell's first
        # connection always kept it.
        self._options = DialOptions(
            max_attempts=1,
            allow_virtual_cell_gateway_host=True,
            socks_proxy_url=config.socks_proxy_url,
        )
        self._codec = codec
        self._clock = clock

    async def dial(self) -> Transport:
        """Make one connection attempt.

        Returns:
            A connected transport.

        Raises:
            waggle.errors.TransportError: The attempt failed: the Queen is not listening (she is
                coming back from a restart, say) or the handshake was rejected.
        """
        transport = WebSocketClientTransport(
            self._url, self._codec, self._clock, options=self._options
        )
        # External await: the WebSocket upgrade, bounded by the transport's own open timeout; a
        # failure raises and is retried by the link's backoff.
        await transport.connect()
        return transport


class CellAnnouncer:
    """Say who this Cell is on a fresh connection, as it did on its first (an `Announcer`)."""

    def __init__(self, source: RealCellSource, config: InCellRuntimeConfig, clock: Clock) -> None:
        """Remember what an announcement is made of.

        Args:
            source: Where the Cell, as it is now, is read from on each announcement.
            config: This Cell's runtime config; its ids, cadence and the link it dials.
            clock: Stamps every frame.
        """
        self._source = source
        self._config = config
        self._clock = clock
        self._runtime_version = collect_version_info().hivemind_version
        self._proxy = SocksProxy.parse(config.socks_proxy_url) if config.socks_proxy_url else None
        self._is_first = True

    async def announce(self, transport: Transport) -> None:
        """Send `cell.ready`, `forage.capacity_report` and `cell.heartbeat` on `transport`.

        Args:
            transport: The connection the dialer just returned.

        Raises:
            waggle.errors.TransportError: The connection failed mid-announcement.
        """
        (cell,) = await self._source.cells()
        config = self._config
        deps = CellLinkDeps(
            transport=transport,
            cell=cell,
            warden_id=config.warden_id,
            hive_id=config.hive_id,
            node_id=config.node_id,
            clock=self._clock,
            heartbeat_interval_s=config.heartbeat_interval_s,
            runtime_version=self._runtime_version,
            attestation=self._attestation(cell.comb_shield),
        )
        trigger = CapacityTrigger.PROVISIONED if self._is_first else CapacityTrigger.RECONNECTED
        await cell_link.send_cell_ready(deps)
        await cell_link.send_capacity_report(deps, cell.capacity, trigger=trigger)
        await cell_link.send_cell_heartbeat(deps)
        # Only after all three went out: an announcement a dying connection cut short is made
        # again as the first, because the Queen has not yet met this Cell on any connection.
        self._is_first = False

    def _attestation(self, tier: CombShieldLevel) -> tuple[AttestationCheck, ...]:
        """Return the checks this Cell's `cell.ready` carries, by its tier (CellReady's rule)."""
        if tier is CombShieldLevel.NIGHT_VEIL:
            # The same on every connection: the facts they judge (the proxy, the onion name) are
            # the config's.
            return cell_link.control_link_attestation(self._config.queen_waggle_url, self._proxy)
        if tier is CombShieldLevel.PROPOLIS:
            # Read afresh on every connection: the routes and resolvers they judge can change
            # while the link is down, and a tiered Cell with no checks would not even construct
            # its CellReady. The Hive's attestation decides readiness.
            return own_tunnel_attestation()
        return ()  # MEADOW attests nothing.


class AfterRedial:
    """The step after a connection is mended: nothing for the first, record and ship after."""

    def __init__(self, reconnected: AfterReconnect) -> None:
        """Wrap the step that records `warden.reconnected` and ships the trail segment.

        Args:
            reconnected: The usual step (`TrailReconnect`), which runs from the second connection.
        """
        self._reconnected = reconnected
        self._is_first = True

    async def reconnected(self, outage_s: float) -> None:
        """Do nothing for the Cell's first connection; otherwise record and ship.

        Args:
            outage_s: How long the link was down, in seconds of the Warden's clock.
        """
        if self._is_first:
            # The Cell has just come up: there was no connection to lose, so no `warden.reconnected`
            # to record, and a trail with nothing in it yet to ship.
            self._is_first = False
            return
        await self._reconnected.reconnected(outage_s)
