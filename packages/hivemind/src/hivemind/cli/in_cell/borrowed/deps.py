"""Compose the link and the WardenDeps of a Warden that runs on a device the Hive borrows.

A colonized device's Warden (Level 1 of the runtime ladder, roadmap step 11.6a) is the Warden the
Hive Stand ran for the device at Level 0, now on the device: the same id, signing as the device's
node. It is built from the same core as a Virtual Cell's Warden (`build_core_deps`, so the two
cannot drift apart) and differs in exactly the places a borrowed machine differs from a container
the Hive owns. Its memory store is the SQLite one under the state directory, so a Handoff survives
its restart. Its lease needs `cell:real:<its Cell>` (the `warden` role's `cell:real:*`, narrowed to
this one machine), which is what the Guard's `lease_creation` point reads. It is a device's Warden
(`DeviceDeps`): it gives its lease back whenever it settles with nothing to supervise, so the
machine keeps no scratch between tasks (ADR-0054), and it holds an `Outage` and a `RemoteDeps`
because its link to the Queen can be lost, unlike the in-process one of a Level 0 Warden; the
`RemoteDeps` carries the budget it may spend sending a Drone to find out why, and the host it is
trying to reach (roadmap 11.9a). And it is
given none of the Virtual Cell's extras: no Queen-relayed snapshotter (a device cannot be
snapshotted), no resident Basket and no Exoskeleton wiring: none of those are the machine's to
give to a Cell it only borrows. It does carry the model servers its manifest declares
(`WardenDeps.servers`), none of which starts at Level 1: the Queen asks for one with a
`swarm.nuc_promote` when she promotes the device to a Nuc (roadmap step 11.8), and the Warden starts
it then through the same `ModelServerHost` every Warden has. `build_device_link` builds the
`ReconnectingQueenLink` the Warden is handed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.borrowed.run`. Calls into `hivemind.cli.in_cell.deps` (`build_core_deps`),
    `hivemind.cli.in_cell.borrowed.dial`/`.stores`/`.config`, `hivemind.guard`, `hivemind.memory`,
    `hivemind.wardens` (WardenDeps, DeviceDeps, the offline package) and waggle only.

Key invariants:
    - The Warden's id, node and Cell are the ones in the config, never freshly minted: the move
      from the Hive Stand keeps the bee the Queen's grants, ceilings and trail are attributed to.
    - The lease capability is scoped to this one Cell by id; the Cell's kind is never consulted
      (codingrules 8.7).
    - The link is built unconnected and the Warden's own trail sync is bound into it afterwards,
      so the reconnection and the Warden ship the trail through one object and never export it at
      once.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the Warden keeping its id.
    - hivemind.cli.compose.swarm.device_warden for the Level 0 Warden this one takes over from.
    - hivemind.cli.in_cell.deps for `build_core_deps`, the shared part.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from hivemind.cell import RealCellSource
from hivemind.cli.in_cell.borrowed.config import BorrowedConfig
from hivemind.cli.in_cell.borrowed.dial import (
    DeviceAnnouncer,
    DeviceDialer,
    DeviceIdentity,
    LateTrailSync,
)
from hivemind.cli.in_cell.borrowed.stores import DeviceStores
from hivemind.cli.in_cell.deps import build_core_deps
from hivemind.guard import Capability, CapabilityFamily
from hivemind.memory import MemoryIdentity
from hivemind.wardens import DeviceDeps
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.offline import (
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

__all__ = ["BorrowedParts", "build_borrowed_warden_deps", "build_device_link"]


@dataclass(frozen=True, slots=True)
class BorrowedParts:
    """The built collaborators a device Warden's deps are made from, bundled for few parameters."""

    source: RealCellSource  # Leases the machine as the Queen's Cell for the device.
    link: ReconnectingQueenLink  # The Warden's link to the Queen; redials when it is lost.
    stores: DeviceStores  # Memory, trail and Leavings under the state directory.


def build_device_link(
    config: BorrowedConfig,
    source: RealCellSource,
    stores: DeviceStores,
    trail_sync: LateTrailSync,
    clock: Clock,
) -> ReconnectingQueenLink:
    """Build the unconnected link to the Queen a device's Warden is handed.

    Args:
        config: The device Warden's configuration.
        source: Where the Cell is read from when the Warden announces itself.
        stores: The Warden's stores; its trail records `warden.reconnected`.
        trail_sync: Ships the trail after a reconnection; bind the Warden's own sync into it.
        clock: Paces the backoff and stamps every frame.

    Returns:
        A ReconnectingQueenLink, OFFLINE until `connect`, over a durable outbox in the state
        directory (which must exist: open the stores first).
    """
    runtime = config.runtime
    codec = Codec(signer=runtime.signer, verifier=runtime.verifier)
    record = WardenRecord(
        trail=stores.trail,
        identity=MemoryIdentity(
            hive_id=runtime.hive_id, node_id=runtime.node_id, actor=str(runtime.warden_id)
        ),
        warden_id=runtime.warden_id,
        clock=clock,
    )
    reconnection = Reconnection(
        dialer=DeviceDialer(runtime.queen_waggle_url, config.tls_pin, codec, clock),
        announcer=DeviceAnnouncer(
            DeviceIdentity(runtime.warden_id, runtime.hive_id, runtime.node_id),
            source,
            config.llm.servers,
            clock,
        ),
        after=TrailReconnect(record, trail_sync),
        clock=clock,
    )
    return ReconnectingQueenLink(reconnection, open_outbox(config.state_dir, codec, clock))


def build_borrowed_warden_deps(
    config: BorrowedConfig, parts: BorrowedParts, clock: Clock
) -> WardenDeps:
    """Build a colonized device's WardenDeps: the shared core, then what a borrowed machine needs.

    Args:
        config: The device Warden's configuration.
        parts: Its source, its link and its stores.
        clock: Injected time source shared by every collaborator this composes.

    Returns:
        A WardenDeps ready for `Warden(config.runtime.warden_id, deps)`.
    """
    runtime = config.runtime
    deps, _registry = build_core_deps(runtime, parts.source, parts.link, parts.stores.trail, clock)
    return dataclasses.replace(
        deps,
        memory=parts.stores.memory,
        servers=config.llm.hostable,  # Declared, not started: a Nuc promotion starts one.
        # The cell's own id names the capability, never its kind: this module built the source.
        lease_capability=Capability(
            family=CapabilityFamily.CELL_REAL, scope=runtime.spawn_config.cell_id
        ),
        device=DeviceDeps(
            release_lease_when_idle=True,
            outage=Outage(),
            remote=RemoteDeps(
                link=parts.link,
                max_offline_s=config.max_offline_s,
                reconnect_budget=config.reconnect_budget,
                peer=config.queen_host,
            ),
        ),
    )
