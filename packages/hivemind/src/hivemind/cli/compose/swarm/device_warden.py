"""Define SwarmWardenFactory: build the Level 0 Warden of one enrolled Swarm device.

The Queen's swarm gate (`hivemind.queen.swarm_gate`) decides *when* a device gets a Warden; this
module is *how* one is made, kept here because a Warden's collaborators are the composition root's
to convert (codingrules section 13). For one node it builds, in order: the Queen<->Warden link (an
in-process memory pair, addressed with the Warden id minted at enrolment, which the node keeps for
life); the device's source (a `SwarmSource`, whose sessions are `PollenSession`s over the packet's
link, wrapped in `LeaseScopedSource` so its command guard holds the set for each lease, and in
`ShieldedSource` when the node's tier is PROPOLIS, so a lease on it is returned only once its
tunnel has been verified); and the Warden's deps, from the part of the Hive Stand's bag any Real
Cell shares (`build_cell_warden_deps`), so the Warden holds nothing of this machine's own disk,
display or models.

Three things differ from the Hive Stand's Warden. Its lease needs `cell:real:<its Cell>` (the
`warden` role's `cell:real:*`, narrowed to the one device), which the Guard's `lease_creation` point
reads. It is a device's Warden (`DeviceDeps`): it gives its lease back whenever it settles with no
task, so the borrowed machine keeps no scratch between tasks, and it keeps an `Outage`, so that a
device that goes away pauses its tasks at a Handoff, holds a bee that dies with the link instead of
counting a crash, and starts them again when the device is back (roadmap 11.9). And the Queen's end
of its link reads the device's capacity from the hub, where the packet's reports land, so grants
are sized from the device as it is now. While idle it watches the device by asking its packet
(`PollenWatchProbe`, roadmap step 11.10), with no lease and no command: there is nothing of the
Hive's on the device to look through. The scratch a lease on the device may hold is the Queen's to
size: the Warden puts the figure in her ceilings on each lease request, and this module hands the
device's source the Hive's `[swarm] scratch_quota_mb` as the figure for a lease those ceilings
do not size (roadmap step 11.4), so the packet's own built-in default never decides it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.swarm`. Built once by
    `build_swarm` and handed to `hivemind.queen.swarm_gate.DeviceWardens`, which calls `build` for
    each device it serves. Calls into `hivemind.cell`, `hivemind.cli.compose.deps` and `.links`,
    `hivemind.cli.compose.swarm.lease_scope`, `hivemind.guard`, `hivemind.queen`,
    `hivemind.swarm`, `hivemind.wardens` and waggle only.

Key invariants:
    - The Warden's id is the node's `warden_id`, so a rebuilt Warden for the same device is the
      same bee to the Queen, the trail and the packet.
    - The Warden is built with no keep root, Royal Jelly access, resident Basket, model server,
      browser launcher or ears: none of those are this machine's to give to a device's Cell. Its
      Exoskeleton's flight recorder is the Hive's own recording store, since the Warden runs here
      and a GUI action on a device is recorded like one on the Hive Stand (ADR-0032).
    - The device's `SwarmSource` is returned as a hub listener, because it must hear the unprompted
      `cell.lease_released` a dead-man switch sends; the gate subscribes it for the Warden's life.
    - Every lease the source asks the device for names a scratch quota: the Queen's ceilings' when
      she set one, else the manifest's.
    - The Warden shares the Hive's one untrusted-content scanner with the Queen and the Hive
      Stand's Warden.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for Level 0.
    - hivemind.cli.compose.deps.warden for build_cell_warden_deps, the bag this one is made from.
    - hivemind.queen.swarm_gate.wardens for DeviceWardenFactory, the Protocol this satisfies.
"""

from __future__ import annotations

from dataclasses import replace

from hivemind.cell import CombShieldLevel, RealCellSource
from hivemind.cli.compose.deps import HiveParts, build_cell_warden_deps
from hivemind.cli.compose.deps.watch import device_watch
from hivemind.cli.compose.links import build_hive_links
from hivemind.cli.compose.swarm.lease_scope import LeaseScopedGuard, LeaseScopedSource
from hivemind.forage import ForageCapacity
from hivemind.guard import Capability, CapabilityFamily
from hivemind.guard.scanner import ContentScanner
from hivemind.queen.deps import LiveCapacity
from hivemind.queen.swarm_gate import DeviceWarden
from hivemind.swarm import (
    GatewayHub,
    PropolisChecks,
    ShieldedSource,
    SwarmNode,
    SwarmRecorder,
    SwarmSource,
)
from hivemind.wardens import DeviceDeps, Warden
from hivemind.wardens.offline import Outage

_BYTES_PER_MB = 1024 * 1024  # `[swarm] scratch_quota_mb` is megabytes; the wire counts bytes.

__all__ = ["SwarmWardenFactory"]


class SwarmWardenFactory:
    """Build the Level 0 Warden of an enrolled device (`DeviceWardenFactory`)."""

    def __init__(
        self, parts: HiveParts, hub: GatewayHub, recorder: SwarmRecorder, scanner: ContentScanner
    ) -> None:
        """Build the factory over the Hive's shared parts and the swarm's hub and recorder.

        Args:
            parts: This Hive's shared collaborators: every Warden reads its stores, policy,
                models, Forage and Guard from them.
            hub: Where a device's live link and freshest record are found.
            recorder: The trail, clock and identity the swarm's events are recorded with.
            scanner: The Hive's one untrusted-content scanner, shared with the Queen.
        """
        self._parts = parts
        self._hub = hub
        self._recorder = recorder
        self._scanner = scanner

    def build(self, node: SwarmNode) -> DeviceWarden:
        """Build `node`'s Warden and the Queen's end of its link; nothing is started.

        Args:
            node: The device as the registry holds it now: its Cell carries these values.

        Returns:
            The Warden, its link, and the device's `SwarmSource` as the hub listener it needs.
        """
        parts, cell = self._parts, node.as_cell()
        manifest = parts.manifest
        links = build_hive_links(
            manifest.hive.id, manifest.hive.node_id, cell, parts.clock, warden_id=node.warden_id
        )
        guard = LeaseScopedGuard(parts.enforcer, self._recorder, node.warden_id, cell)
        swarm_source = SwarmSource(
            node,
            self._hub,
            self._recorder,
            guard,
            default_scratch_quota_bytes=manifest.swarm.scratch_quota_mb * _BYTES_PER_MB,
        )
        # The cell's own id names the capability, never its kind: this package built the source.
        lease = Capability(family=CapabilityFamily.CELL_REAL, scope=cell.id)
        deps = build_cell_warden_deps(
            parts, self._shielded(node, LeaseScopedSource(swarm_source, guard)), links, lease
        )
        watch = device_watch(parts, node, self._hub)  # Roadmap step 11.10: it asks the packet.
        # A fresh outage per Warden: it holds the tasks paused for this one device's loss.
        device = DeviceDeps(lease_retry_s=manifest.swarm.heartbeat_interval_s, outage=Outage())
        # It runs here, so the flight recorder keeps a device's Exoskeleton actions where it keeps
        # the Hive Stand's (ADR-0032); a browser launcher or ears would act on this machine.
        recordings = parts.stores.recordings
        deps = replace(
            deps, scanner=self._scanner, device=device, watch=watch, recording_store=recordings
        )
        link = replace(links.queen_link, live_capacity=self._live_capacity(node))
        return DeviceWarden(Warden(node.warden_id, deps), link, hub_listeners=(swarm_source,))

    def _shielded(self, node: SwarmNode, source: RealCellSource) -> RealCellSource:
        """Wrap `source` so a PROPOLIS lease is returned only once the tunnel is verified."""
        if node.comb_shield is not CombShieldLevel.PROPOLIS:
            return source
        return ShieldedSource(source, PropolisChecks(self._recorder))

    def _live_capacity(self, node: SwarmNode) -> LiveCapacity:
        """Return the reader of the device's capacity as its latest report left it on the hub."""
        hub, built = self._hub, node

        async def read() -> ForageCapacity:
            """Read the hub's freshest record of the device, else the one this Warden has."""
            # No I/O: the packet's `forage.capacity_report` frames update the hub's record.
            latest = hub.node(built.device_id)
            return (latest or built).capacity

        return read
