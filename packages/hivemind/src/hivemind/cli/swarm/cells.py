"""Read the Swarm's Cells for `hive cells list`: each enrolled device as a Real Cell.

`hive cells list` shows the Cells the Hive can offer: the Hive Stand's own (the machine the Queen
runs on) and, since phase 11, every enrolled device (an existing machine the Hive borrows as a
Real Cell). A device is a Cell of kind REAL and source `swarm`, and its row carries what only the
Swarm knows about it: where its Warden runs (`hive_stand` while the device is on the GATEWAY rung
of the runtime ladder, `on_cell` once the Warden has moved onto it, COLONIZED and NUC: the same
rule the Fleet view applies), the rung itself, and the Warden's mode. The device's access level,
Comb Shield tier and capacity are the Cell's own. The mode is read from the newest `warden.*` mode
edge on the trail (`warden.active`, `warden.watch` or `warden.offline`); a Warden that has never
recorded one, or whose newest edge left it in no mode (clustered, migrated, stopped), is
`UNKNOWN`. A revoked device is not a Cell any more (the Hive forgets it), so it is not listed; it
stays in `hive swarm list` for the audit.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Called by
    `hivemind.cli.readback.cells` (`hive cells list`). Calls into `hivemind.cell`,
    `hivemind.cli.stores`, `hivemind.manifest`, `hivemind.observation` (the shared Warden-location
    rule), `hivemind.pheromone` and `hivemind.swarm` only.

Key invariants:
    - Read-only, and silent when there is nothing to read: a manifest that keeps the Swarm off, or
      a Hive whose database does not exist yet, has no Swarm Cells and creates no file.
    - The Warden location comes from `warden_runs_at`, the rule the Fleet view uses, so the two
      cannot disagree.
    - A device is `enabled` (may be leased now) exactly when the Hive can reach it: its status is
      ONLINE.

See Also:
    - hivemind.cli.readback.cells for the command and the table these facts become rows of.
    - hivemind.observation.api.fleet for the Fleet view's `warden_runs`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.cell import Cell
from hivemind.cli.stores import open_swarm_registry, open_trail
from hivemind.manifest import HiveManifest
from hivemind.observation import warden_runs_at
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.swarm import NodeStatus, SwarmRegistry
from waggle.messages.swarm import RuntimeLevel

MODE_UNKNOWN = "UNKNOWN"  # No Warden mode edge on the trail, or the newest one left no mode.
EDGE_LOOKBACK = 25  # Newest events read per Warden to find its latest mode edge.
# The trail's three edges that put a Warden in a mode the table can name.
_MODE_OF_EDGE = {"warden.active": "ACTIVE", "warden.watch": "WATCH", "warden.offline": "OFFLINE"}
# The edges that take a Warden out of every mode: it is paused, moving or gone, not working.
_NO_MODE_EDGES = frozenset({"warden.clustered", "warden.migrated", "warden.stopped"})

__all__ = ["EDGE_LOOKBACK", "MODE_UNKNOWN", "SwarmCellFacts", "mode_of", "read_swarm_cells"]


@dataclass(frozen=True, slots=True)
class SwarmCellFacts:
    """One enrolled device as a Cell, with what only the Swarm knows about it.

    Attributes:
        cell: The device's Real Cell (`SwarmNode.as_cell`): source `swarm`, its access level, tier
            and capacity.
        warden_runs: Where its Warden runs: `hive_stand` or `on_cell`.
        level: The rung of the runtime ladder it is on.
        mode: ACTIVE, WATCH, OFFLINE or UNKNOWN.
        is_online: Whether the Hive can reach it now, so its Cell may be leased.
    """

    cell: Cell
    warden_runs: str
    level: RuntimeLevel
    mode: str
    is_online: bool


def read_swarm_cells(manifest: HiveManifest) -> tuple[SwarmCellFacts, ...]:
    """Read every enrolled, unrevoked device as a Cell, oldest enrolment first.

    Must run outside any event loop: the stores are opened with their own `asyncio.run`.

    Args:
        manifest: The Hive Manifest; `[swarm] enabled` and `[hive] db` decide whether there is
            anything to read.

    Returns:
        One entry per device; empty when the Swarm is off or the Hive has no database yet.
    """
    db = manifest.resolve_path(manifest.hive.db)
    # A read command makes no files: a Hive that never ran has no devices to list.
    if not manifest.swarm.enabled or not db.exists():
        return ()
    return asyncio.run(_read(open_swarm_registry(db), open_trail(db)))


def mode_of(edges_newest_first: Sequence[PheromoneEvent]) -> str:
    """Say what mode a Warden is in, from its trail events, newest first.

    Args:
        edges_newest_first: The `warden.*` events about one Warden, newest first.

    Returns:
        ACTIVE, WATCH or OFFLINE from the newest mode edge; UNKNOWN when there is none, or the
        newest edge left the Warden in no mode.
    """
    for event in edges_newest_first:
        if event.kind in _MODE_OF_EDGE:
            return _MODE_OF_EDGE[event.kind]
        # The newest edge that says anything about the mode decides: a Warden that was clustered,
        # moved or stopped after it last worked is in no mode now, whatever it did before.
        if event.kind in _NO_MODE_EDGES:
            return MODE_UNKNOWN
    return MODE_UNKNOWN


async def _read(registry: SwarmRegistry, trail: PheromoneTrail) -> tuple[SwarmCellFacts, ...]:
    """Join each unrevoked device with its Warden's newest mode edge."""
    facts: list[SwarmCellFacts] = []
    for node in await registry.nodes():
        if node.status is NodeStatus.REVOKED:
            continue
        # Latency: one local indexed trail read per device.
        edges = await trail.query(
            TrailQuery(
                family="warden", subject_id=node.warden_id, newest_first=True, limit=EDGE_LOOKBACK
            )
        )
        facts.append(
            SwarmCellFacts(
                cell=node.as_cell(),
                warden_runs=warden_runs_at(node.level),
                level=node.level,
                mode=mode_of(edges),
                is_online=node.status is NodeStatus.ONLINE,
            )
        )
    return tuple(facts)
