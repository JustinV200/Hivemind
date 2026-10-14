"""Build a Night Veil Cell's mending link to the Queen, and the Warden deps that go with it.

Tor is how a Night Veil Cell reaches the Queen, and Tor can die under it (killed, crashed, starved
of circuits) while the VPN and the default route stay up. The Cell must then neither fall back to a
direct or VPN-only connection to the Hive Stand, which would put its Waggle traffic where the tier
promises it never is (codingrules 8.7), nor drop its work on the floor: it goes cut off, the same
as a Warden that dials the Queen from a device does (codingrules 8.8, ADR-0053). `connect_redialing`
builds that Warden's link, a `ReconnectingQueenLink` over `ProxyDialer` and `CellAnnouncer`, and
makes its first connection, so the Cell announces itself exactly as before; if Tor is not yet
answering, it keeps trying through the proxy, with backoff, until the Queen's own ready limit tears
the Cell down. `RedialingLink.equip` then gives the built Warden what being cut off needs: an
`Outage` to remember the tasks it pauses, and the link and offline limit (`HIVEMIND_MAX_OFFLINE_S`,
the Hive's `[supervision] max_offline_s`) in `DeviceDeps`. Past that limit the Warden clusters fully
(`hivemind.wardens.offline.freeze`); when Tor returns, the link redials, announces the Cell again
and replays what was queued, and the Queen, who detached the Warden when its connection ended,
takes it back as a reconnection. The outbox that queues results and Alarms meanwhile is a file
below the Cell's scratch root, which is destroyed with the Cell, and a Night Veil Cell is torn down
at its task's end.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.redial`. Called by
    `hivemind.cli.in_cell.main.run_in_cell_warden` for a Night Veil Cell. Calls into
    `hivemind.cli.in_cell` (the config, the borrowed trail hand-off), `hivemind.memory`,
    `hivemind.pheromone`, `hivemind.wardens` and waggle only.

Key invariants:
    - `connect_redialing` returns only once the Cell has announced itself to the Queen over Tor;
      nothing is sent on the link before that.
    - The link redials through the proxy and nothing else (`ProxyDialer`): whether it is up, down
      or recovering, no frame reaches the Queen another way.
    - The Warden's own trail sync is bound into the link by `equip`, so the reconnection and the
      Warden's heartbeat ship the Cell's trail through one object and never export it at once.
    - Only a Night Veil Cell is built here (`redials`); every other Cell keeps its bare transport.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for what being cut off does.
    - hivemind.cli.in_cell.borrowed.deps for the same composition on a colonized device.
    - hivemind.cli.in_cell.main for the one caller.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from hivemind.cell import CombShieldLevel, RealCellSource
from hivemind.cli.in_cell.borrowed.dial import LateTrailSync
from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.cli.in_cell.redial.dial import AfterRedial, CellAnnouncer, ProxyDialer
from hivemind.memory import MemoryIdentity
from hivemind.pheromone import PheromoneTrail
from hivemind.wardens import DeviceDeps
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.offline import (
    OfflineOutbox,
    Outage,
    ReconnectingQueenLink,
    Reconnection,
    RemoteDeps,
    TrailReconnect,
    WardenRecord,
    open_outbox,
)
from waggle.clock import Clock
from waggle.codec import Codec

OUTBOX_DIR = "outbox"  # Below the scratch root, like the resident Basket's own directory.

__all__ = ["OUTBOX_DIR", "RedialingLink", "connect_redialing", "redials"]


def redials(config: InCellRuntimeConfig) -> bool:
    """Whether this Cell's link to the Queen is one that mends: a Night Veil Cell's, over Tor.

    Args:
        config: This Cell's runtime config.

    Returns:
        True for the tier whose control link rides a Tor circuit that can die under it. Every
        other Cell keeps the bare transport it always had; a Meadow Cell's link is its host's.
    """
    return config.spawn_config.comb_shield is CombShieldLevel.NIGHT_VEIL


@dataclass(frozen=True, slots=True)
class RedialingLink:
    """A connected, announced mending link, and what the Warden built over it needs."""

    link: ReconnectingQueenLink  # The Warden's link to the Queen; redials when Tor drops it.
    trail_sync: (
        LateTrailSync  # Ships the trail after a reconnection, through the Warden's own sync.
    )
    max_offline_s: float  # How long the link may stay down before the Warden clusters fully.
    peer: str  # The Queen's onion host, as the Warden names the peer it cannot reach.

    def equip(self, deps: WardenDeps) -> WardenDeps:
        """Give a Warden built over this link what a Warden that can be cut off needs.

        Args:
            deps: The Warden's deps, built with `link` as their Queen link.

        Returns:
            `deps` with an `Outage` and the link's state in its `DeviceDeps`, and the Warden's own
            trail sync bound into the link. The lease is kept when the Warden idles (a Virtual
            Cell's is not a machine it borrows), and no search is made for why the link went: a
            Night Veil Cell sends no Drone to look into its own Tor circuit.
        """
        outage = Outage()
        # A change of the link wakes the Warden's tick at once, where it would otherwise notice at
        # its next heartbeat; the limit is then counted from the moment the link actually dropped.
        self.link.watch(outage.nudge)
        self.trail_sync.bind(deps.trail_sync)
        device = DeviceDeps(
            release_lease_when_idle=False,
            outage=outage,
            remote=RemoteDeps(link=self.link, max_offline_s=self.max_offline_s, peer=self.peer),
        )
        return dataclasses.replace(deps, device=device)


async def connect_redialing(
    config: InCellRuntimeConfig, source: RealCellSource, trail: PheromoneTrail, clock: Clock
) -> RedialingLink:
    """Build the Cell's mending link and make its first connection through Tor.

    Args:
        config: This Cell's runtime config; a Night Veil one (`redials`).
        source: Where the Cell is read from when it announces itself.
        trail: The Cell's own trail segment, in which `warden.reconnected` is recorded.
        clock: Paces the redial backoff and stamps every frame.

    Returns:
        The link, announced and online, with the pieces `RedialingLink.equip` binds.

    Raises:
        OSError: The outbox directory below the scratch root cannot be made or opened.
        OutboxError: The outbox file is corrupt beyond a torn last line.
    """
    codec = Codec(signer=config.signer, verifier=config.verifier)
    trail_sync = LateTrailSync()
    record = WardenRecord(
        trail=trail,
        identity=MemoryIdentity(
            hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
        ),
        warden_id=config.warden_id,
        clock=clock,
    )
    reconnection = Reconnection(
        dialer=ProxyDialer(config, codec, clock),
        announcer=CellAnnouncer(source, config, clock),
        after=AfterRedial(TrailReconnect(record, trail_sync)),
        clock=clock,
    )
    outbox_dir = config.spawn_config.scratch_root / OUTBOX_DIR
    # Off the loop: a directory and a file on the Cell's disk, brief but blocking.
    outbox = await asyncio.to_thread(_open_outbox, outbox_dir, codec, clock)
    link = ReconnectingQueenLink(reconnection, outbox)
    # External await: dials with backoff for as long as Tor is not answering, which is why the
    # Queen's ready limit, not this call, is what ends a Cell that never connects.
    await link.connect()
    return RedialingLink(
        link=link,
        trail_sync=trail_sync,
        max_offline_s=config.max_offline_s,
        peer=urlsplit(config.queen_waggle_url).hostname or "",
    )


def _open_outbox(directory: Path, codec: Codec, clock: Clock) -> OfflineOutbox:
    """Make `directory` if need be, and open the durable queue in it."""
    directory.mkdir(parents=True, exist_ok=True)
    return open_outbox(directory, codec, clock)
