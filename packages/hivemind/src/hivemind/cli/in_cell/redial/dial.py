"""Dial the Queen's onion service through Tor and announce a Night Veil Cell on each connection.

A Night Veil Cell's link to the Queen is a Tor circuit to her hidden service, and Tor can die under
it. `ProxyDialer` is the dial the link redials with (a `QueenDialer`): one attempt, always through
the SOCKS proxy the Cell's bootstrap names, with nothing in it that could connect any other way.
There is no direct address in it to fall back to (the Cell knows only the onion name, which no
resolver answers), no second route, and no option that turns the proxy off; a proxy that is down is
a failed attempt, and the link's backoff tries the same proxy again. `CellAnnouncer` is what the
Cell says on a fresh connection (an `Announcer`): the `cell.ready` with its control-link checks, the
capacity it reports and a heartbeat, in the order the Queen's readiness gate reads them, exactly as
the Cell's first connection did. `AfterRedial` is the step after a connection is mended: the first
connection of a Cell's life is not a reconnection, so it records and ships nothing; every later one
records `warden.reconnected` and ships the trail segment (`hivemind.wardens.offline`).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.redial`. Built by
    `hivemind.cli.in_cell.redial.build` and handed to `ReconnectingQueenLink`. Calls into
    `hivemind.cell` (RealCellSource), `hivemind.cli.in_cell` (the three frames, the config),
    `hivemind.cli.version`, `hivemind.wardens.offline` and waggle only.

Key invariants:
    - Every dial goes through the SOCKS proxy: `ProxyDialer` cannot be built without one, and the
      transport it makes refuses an onion URI that names none.
    - A dial is one attempt, bounded by the transport's own open timeout and its SOCKS timeouts; a
      failed one raises a `TransportError` the link's backoff retries.
    - The first frame on every connection is `cell.ready`, signed with the Cell's node key; a
      re-announcement describes the Cell as it is now (`source.cells()`).
    - The control-link checks in `cell.ready` are those of a link that dials the Queen only
      through the proxy, at her onion service (`hivemind.cli.in_cell.link`), on every connection.

See Also:
    - hivemind.wardens.offline.reconnect for the QueenDialer, Announcer and AfterReconnect
      Protocols.
    - hivemind.cli.in_cell.borrowed.dial for the device's counterpart of these three.
    - waggle.transport.websocket_client for the dial itself and why it never falls back.
"""

from __future__ import annotations

from hivemind.cell import RealCellSource
from hivemind.cli.in_cell import link as cell_link
from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.cli.in_cell.link import CellLinkDeps
from hivemind.cli.version import collect_version_info
from hivemind.wardens.offline import AfterReconnect
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.messages.forage import CapacityTrigger
from waggle.transport.base import Transport
from waggle.transport.socks import SocksProxy
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

__all__ = ["AfterRedial", "CellAnnouncer", "ProxyDialer"]


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
        proxy = SocksProxy.parse(config.socks_proxy_url) if config.socks_proxy_url else None
        # The two checks a Night Veil Cell makes of its own link, which are the same on every
        # connection: the facts they judge (the proxy, the onion name) are the config's.
        self._attestation = cell_link.control_link_attestation(config.queen_waggle_url, proxy)
        self._is_first = True

    async def announce(self, transport: Transport) -> None:
        """Send `cell.ready`, `forage.capacity_report` and `cell.heartbeat` on `transport`.

        Args:
            transport: The connection `ProxyDialer.dial` just returned.

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
            attestation=self._attestation,
        )
        trigger = CapacityTrigger.PROVISIONED if self._is_first else CapacityTrigger.RECONNECTED
        await cell_link.send_cell_ready(deps)
        await cell_link.send_capacity_report(deps, cell.capacity, trigger=trigger)
        await cell_link.send_cell_heartbeat(deps)
        # Only after all three went out: an announcement a dying connection cut short is made
        # again as the first, because the Queen has not yet met this Cell on any connection.
        self._is_first = False


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
