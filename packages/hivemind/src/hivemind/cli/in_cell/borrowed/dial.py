"""Dial the swarm listener and announce a colonized device's Cell on each new connection.

A Warden that dials the Queen from a device holds its link through `ReconnectingQueenLink`
(`hivemind.wardens.offline`), which redials on a lost connection through an injected dialer and
says who the Cell is again through an injected announcer. This module is those two, specific to a
device: `DeviceDialer` opens one pinned, signed connection to the swarm listener (one attempt;
the link owns the backoff between attempts), and `DeviceAnnouncer` sends the three frames the
Queen's readiness gate and capacity books start from, `cell.ready`, `forage.capacity_report` and
`cell.heartbeat`, in that order. `cell.ready` carries the level the Queen granted the device and
its tier, as the Cell the lease source reports them, and the capacity report carries the Cell's
live figures and the model servers the device manifest declares (none of them started), so the
Queen can judge the device's eligibility to become a Nuc. `LateTrailSync` lets the link, which is
built before the Warden's deps (the Warden is handed the link), ship the trail through the very
`TrailSync` the Warden's deps will hold, so only one export is ever on the wire.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Built by
    `hivemind.cli.in_cell.borrowed.run` and handed to `ReconnectingQueenLink`. Calls into
    `hivemind.cell` (RealCellSource), `hivemind.cli.in_cell.link` (the frames),
    `hivemind.cli.version`, `hivemind.wardens.trail_sync` and waggle only.

Key invariants:
    - A dial is one attempt, bounded by the transport's own open timeout; a failed one raises a
      `TransportError` the link's backoff retries.
    - The first frame on every connection is `cell.ready`, signed with the device's node key, as
      the Queen's listener requires of a Warden that attaches.
    - A re-announcement describes the Cell as it is now (`source.cells()`), not as it was at
      start, because the Queen's copy of its capacity may be stale after an outage.
    - The announcement's trigger is always RECONNECTED: it is the Warden saying again, to a Queen
      that may know it already, what the Cell is.

See Also:
    - hivemind.wardens.offline.reconnect for the QueenDialer and Announcer Protocols.
    - hivemind.cli.in_cell.link for the three frames this sends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from hivemind.cell import RealCellSource
from hivemind.cli.in_cell import link as cell_link
from hivemind.cli.in_cell.config import DEFAULT_HEARTBEAT_INTERVAL_S
from hivemind.cli.in_cell.link import CellLinkDeps
from hivemind.cli.version import collect_version_info
from hivemind.wardens.trail_sync import TrailSync
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.ids import HiveId, NodeId, WardenId
from waggle.messages.forage import CapacityTrigger, ModelServerReport
from waggle.transport.base import Transport
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport

__all__ = ["DeviceAnnouncer", "DeviceDialer", "DeviceIdentity", "LateTrailSync"]


class DeviceDialer:
    """Open one pinned, signed connection to the swarm listener (a `QueenDialer`)."""

    def __init__(self, url: str, tls_pin: str | None, codec: Codec, clock: Clock) -> None:
        """Remember where to dial and how; nothing is dialled until `dial`.

        Args:
            url: The swarm listener's URL: `wss://` anywhere, or `ws://` on loopback.
            tls_pin: The listener's certificate fingerprint, for a `wss://` URL.
            codec: Signs with the device's node key and verifies with the Queen's.
            clock: Paces the transport.
        """
        self._url = url
        self._options = DialOptions(max_attempts=1, tls_pin_sha256=tls_pin)
        self._codec = codec
        self._clock = clock

    async def dial(self) -> Transport:
        """Make one connection attempt.

        Returns:
            A connected transport.

        Raises:
            waggle.errors.TransportError: The attempt failed: refused, unreachable, a wrong
                certificate or a refused handshake.
        """
        transport = WebSocketClientTransport(
            self._url, self._codec, self._clock, options=self._options
        )
        # External await: a TCP connect, a TLS handshake against the pin and a WebSocket upgrade,
        # bounded by the transport's own per-attempt open timeout; a failure raises and is retried
        # by the link's backoff, never by this method.
        await transport.connect()
        return transport


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Who the Warden is on the wire: the three ids its frames are addressed and signed by."""

    warden_id: WardenId  # The sender of every frame; kept across the move from the Hive Stand.
    hive_id: HiveId  # The Queen's bee address: the recipient.
    node_id: NodeId  # The device's node, whose key signs.


class DeviceAnnouncer:
    """Say who this Cell is on a fresh connection (an `Announcer`)."""

    def __init__(
        self,
        identity: DeviceIdentity,
        source: RealCellSource,
        servers: tuple[ModelServerReport, ...],
        clock: Clock,
    ) -> None:
        """Remember what the announcement is made of.

        Args:
            identity: The Warden's ids.
            source: Where the Cell, with its live figures, is read from on each announcement.
            servers: The model servers the device manifest declares, none of them started.
            clock: Stamps every frame.
        """
        self._identity = identity
        self._source = source
        self._servers = servers
        self._clock = clock
        self._runtime_version = collect_version_info().hivemind_version

    async def announce(self, transport: Transport) -> None:
        """Send `cell.ready`, `forage.capacity_report` and `cell.heartbeat` on `transport`.

        Args:
            transport: The connection `DeviceDialer.dial` just returned.

        Raises:
            waggle.errors.TransportError: The connection failed mid-announcement.
        """
        (cell,) = await self._source.cells()
        deps = CellLinkDeps(
            transport=transport,
            cell=cell,
            warden_id=self._identity.warden_id,
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            clock=self._clock,
            heartbeat_interval_s=DEFAULT_HEARTBEAT_INTERVAL_S,  # What the Queen judges liveness by.
            runtime_version=self._runtime_version,
        )
        await cell_link.send_cell_ready(deps)
        await cell_link.send_capacity_report(
            deps,
            cell.capacity,
            trigger=CapacityTrigger.RECONNECTED,
            model_servers=self._servers,
        )
        await cell_link.send_cell_heartbeat(deps)


class LateTrailSync:
    """A `TrailSync` that ships through another one, bound once the Warden's deps exist.

    The reconnecting link is built first (the Warden is handed it), but the trail sync the link
    ships through after a reconnection must be the Warden's own, so that the reconnection and the
    Warden's heartbeat never export the same segment at once. Until bound, there is nothing to ship.
    """

    def __init__(self) -> None:
        """Start unbound."""
        self._target: TrailSync | None = None

    def bind(self, target: TrailSync | None) -> None:
        """Ship through `target` from now on; None leaves nothing to ship."""
        self._target = target

    async def sync(self) -> bool:
        """Ship the trail segment through the bound sync.

        Returns:
            What that sync returned; True when none is bound (nothing was lost by waiting).
        """
        if self._target is None:
            return True
        return await self._target.sync()

    def rewind(self, window: timedelta) -> None:
        """Have the bound sync ship the last `window` again; nothing to do while unbound."""
        if self._target is not None:
            self._target.rewind(window)
