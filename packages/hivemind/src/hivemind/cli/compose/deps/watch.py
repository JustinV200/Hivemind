"""Build the WatchDeps a Warden watches its idle Cell with, from `[supervision.watch]`.

A Warden with no bees working watches its Cell (ADR-0054), and what it looks through depends on
where it runs relative to the Cell. The Hive Stand's own Warden is local to its Cell, so it looks
through its own session (`SessionWatchProbe`, which the Warden lends its session to as it opens
one: `bind_session`), and reads the directories `[supervision.watch] roots` names. A Swarm device's
Warden runs on the Hive Stand with only a Pollen Packet on the device, so it asks the packet
(`PollenWatchProbe`), and names no roots: the packet answers only for the paths the device's own
capabilities grant, and the Hive's manifest knows nothing of another machine's directories. Both
share the numbers in `[supervision.watch]`. The Hive Stand has no device, but an Observation
carries a device id, so the probe is given one derived from the Hive Stand's Cell id.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.deps`. Called by
    `hivemind.cli.compose.deps.warden.build_warden_deps` and
    `hivemind.cli.compose.swarm.device_warden.SwarmWardenFactory`. Calls into `hivemind.cell`,
    `hivemind.cli.compose.deps.parts`, `hivemind.manifest`, `hivemind.swarm`,
    `hivemind.wardens.watch` and waggle only.

Key invariants:
    - A probe is chosen by where its Warden runs, never by looking at what kind of Cell it has.
    - A device's Warden watches no root the Hive Stand's manifest names.
    - The Hive Stand's device id is a pure function of its Cell id: the same on every run.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode.
    - hivemind.manifest.schema.supervision.WatchSection for the numbers' source.
    - hivemind.wardens.watch for WatchDeps and the probes.
"""

from __future__ import annotations

from hivemind.cell import hive_stand_cell_id
from hivemind.cli.compose.deps.parts import HiveParts
from hivemind.manifest import HiveManifest
from hivemind.swarm import GatewayHub, PollenWatchProbe, SwarmNode
from hivemind.wardens.watch import SessionWatchProbe, WatchDeps, WatchSettings
from waggle.ids import CellId, DeviceId, IdKind, parse_id

__all__ = ["device_id_of_cell", "device_watch", "hive_stand_watch", "watch_settings"]


def watch_settings(manifest: HiveManifest, *, with_roots: bool) -> WatchSettings:
    """Convert `[supervision.watch]` into the numbers a watching Warden runs by.

    Args:
        manifest: The loaded Hive Manifest.
        with_roots: Whether the manifest's `roots` apply: true for the Hive Stand's own Warden,
            false for a device's, whose directories are not this machine's.

    Returns:
        The settings.
    """
    watch = manifest.supervision.watch
    return WatchSettings(
        interval_s=watch.interval_s,
        retention_s=watch.retention_s,
        patrol_interval_s=watch.patrol_interval_s,
        cpu_share_alarm=watch.cpu_share_alarm,
        disk_fill_alarm=watch.disk_fill_alarm,
        roots=watch.roots if with_roots else (),
    )


def device_id_of_cell(cell_id: CellId) -> DeviceId:
    """Derive the device id the Hive Stand's own Observations carry from its Cell id.

    Args:
        cell_id: A `cell_<ulid>` id.

    Returns:
        The `device_<same ulid>` id: stable for the Cell, and a valid device id.
    """
    ulid = cell_id.removeprefix(f"{IdKind.CELL.value}_")
    return DeviceId(parse_id(f"{IdKind.DEVICE.value}_{ulid}", IdKind.DEVICE))


def hive_stand_watch(parts: HiveParts) -> WatchDeps:
    """Build the Hive Stand's own Warden's WatchDeps: it looks through its own session.

    Args:
        parts: This Hive's shared collaborators.

    Returns:
        WatchDeps over a `SessionWatchProbe` that the Warden lends its session to.
    """
    manifest = parts.manifest
    cell_id = hive_stand_cell_id(manifest.hive.node_id)
    probe = SessionWatchProbe(device_id_of_cell(cell_id), parts.clock)
    return WatchDeps(
        probe=probe,
        settings=watch_settings(manifest, with_roots=True),
        bind_session=probe.use_session,
    )


def device_watch(parts: HiveParts, node: SwarmNode, hub: GatewayHub) -> WatchDeps:
    """Build a Swarm device's Warden's WatchDeps: it asks the device's packet.

    Args:
        parts: This Hive's shared collaborators.
        node: The device.
        hub: Where the device's live link is found.

    Returns:
        WatchDeps over a `PollenWatchProbe`, with no roots and no session to lend.
    """
    return WatchDeps(
        probe=PollenWatchProbe(node, hub),
        settings=watch_settings(parts.manifest, with_roots=False),
    )
