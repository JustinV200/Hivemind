"""Gather what one Absconding would tear down, from backend labels and the trail alone.

Roadmap step 13.4: `hive abscond` covers Virtual Cells (live and dormant), every lease on the Hive
Stand and on devices, every grant, Worker processes, temp dirs and resident Basket blobs on every
Cell, and lists the Leavings (paths left outside scratch on purpose) for the human to tick. It
must work when the Brood Chamber is corrupt or the Queen is gone (codingrules 15), so nothing here
reads a lifecycle table: Virtual Cells come from every backend's own labels, leases and Workers
from the trail, grants from the ledger's own store, residency and Leavings from their tables.
`gather_scope` only reads; `hivemind.cli.abscond.execute` acts on what the human confirmed.

A lease is the Hive Stand's when the Hive Stand's own node recorded it, and a device's otherwise
(a Swarm device's Warden ships its segment, ADR-0053). A Worker is unfinished when the trail shows
it spawned and never done, failed or killed. A temp dir is a scratch directory under
`[hive_stand] scratch_root` whose name is a lease id and whose lease is not open; none is listed
while `keep_scratch` keeps scratch on purpose, or while a Queen looks to be running (her next
lease's scratch may exist a moment before its `cell.leased` does).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.abscond`. Called by
    `.command`. Calls into `hivemind.basket.store`, `hivemind.cell.leavings`,
    `hivemind.cli.compose.virtual_cells`, `hivemind.cli.readback.virtual_offline`, `hivemind.hive`,
    `hivemind.manifest`, `hivemind.pheromone`, `hivemind.queen.forage.ledger` and waggle only.

Key invariants:
    - Reads only: nothing here changes a Cell, a lease, a grant, a file or a row.
    - A temp dir is listed only when its name parses as a lease id, so a name read off the disk
      can never point outside the scratch root.

See Also:
    - .claude/roadmap.md step 13.4.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from hivemind.basket.store import BasketStore
from hivemind.cell.leavings import Leaving, LeavingsStore
from hivemind.cli.compose.virtual_cells import VirtualCellsParts
from hivemind.cli.readback.virtual_offline import (
    LeaseOrphan,
    list_all_virtual,
    open_real_leases,
    queen_likely_running,
)
from hivemind.hive import VirtualCellRecord, VirtualCellStatus
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneTrail, TrailQuery
from hivemind.queen.forage.ledger import ForageLedger
from waggle.errors import InvalidIdError
from waggle.ids import CellId, IdKind, parse_id

SPAWNED_KIND = "worker.spawned"  # A sub-bee started, its subject the bee.
ENDED_KINDS = ("worker.done", "worker.failed", "worker.killed")  # How a sub-bee ends.
CELL_KINDS = ("cell.provisioned", "cell.leased")  # Every Cell the Hive ever held.

__all__ = ["AbscondScope", "ScopeSources", "gather_scope"]


@dataclass(frozen=True, slots=True)
class ScopeSources:
    """What the scope is read from.

    Attributes:
        manifest: The loaded Hive Manifest.
        trail: The Hive's trail.
        ledger: The Forage ledger, restored from its store.
        virtual_cells: The Virtual side, or None when no backend is configured.
        basket: The Basket store, for resident blobs.
        leavings: The Leavings ledger.
    """

    manifest: HiveManifest
    trail: PheromoneTrail
    ledger: ForageLedger
    virtual_cells: VirtualCellsParts | None
    basket: BasketStore
    leavings: LeavingsStore


@dataclass(frozen=True, slots=True)
class AbscondScope:
    """Everything one Absconding would tear down, and the Leavings the human may tick.

    Attributes:
        cells: Every Virtual Cell the backends list for this Hive, with its backend's name.
        stand_leases: Open leases the Hive Stand's own node recorded.
        device_leases: Open leases a device's node recorded.
        grant_ids: Every live Forage grant.
        workers: Every Worker the trail shows spawned and never ended.
        temp_dirs: Scratch directories no open lease owns.
        resident: Every `(Cell, blob hash)` the Basket holds resident.
        leavings: Every active Leaving, in the order the review numbers them from 1.
        queen_running: Whether a Queen looks to be running (her leases' release is deferred).
    """

    cells: tuple[tuple[str, VirtualCellRecord], ...]
    stand_leases: tuple[LeaseOrphan, ...]
    device_leases: tuple[LeaseOrphan, ...]
    grant_ids: tuple[str, ...]
    workers: tuple[str, ...]
    temp_dirs: tuple[Path, ...]
    resident: tuple[tuple[CellId, str], ...]
    leavings: tuple[Leaving, ...]
    queen_running: bool

    @property
    def dormant(self) -> int:
        """How many of `cells` are dormant (paused for reuse)."""
        return sum(1 for _, record in self.cells if record.status is VirtualCellStatus.DORMANT)


async def gather_scope(sources: ScopeSources) -> AbscondScope:
    """Read everything one Absconding would tear down.

    Args:
        sources: The manifest and the stores to read.

    Returns:
        The scope; nothing has changed.
    """
    manifest, trail = sources.manifest, sources.trail
    cells: tuple[tuple[str, VirtualCellRecord], ...] = ()
    if sources.virtual_cells is not None:
        cells = await list_all_virtual(sources.virtual_cells.registry, manifest.hive.id)
    leases = await open_real_leases(trail)
    stand = tuple(lease for lease in leases if lease.node_id in ("", manifest.hive.node_id))
    devices = tuple(lease for lease in leases if lease not in stand)
    running = await queen_likely_running(trail)
    # A running Queen may be opening a lease whose scratch exists before its cell.leased does.
    temp_dirs = () if running else await _temp_dirs(manifest, {lease.lease_id for lease in leases})
    return AbscondScope(
        cells=cells,
        stand_leases=stand,
        device_leases=devices,
        grant_ids=tuple(str(grant.id) for grant in sources.ledger.live_grants()),
        workers=await _unfinished_workers(trail),
        temp_dirs=temp_dirs,
        resident=await _resident(sources, cells),
        leavings=await sources.leavings.list_all_leavings(),
        queen_running=running,
    )


async def _unfinished_workers(trail: PheromoneTrail) -> tuple[str, ...]:
    """Every Worker the trail shows spawned and never done, failed or killed."""
    spawned = await trail.query(TrailQuery(kind=SPAWNED_KIND, limit=MAX_QUERY_LIMIT))
    ended: set[str] = set()
    for kind in ENDED_KINDS:
        events = await trail.query(TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT))
        ended.update(event.subject_id for event in events)
    return tuple(dict.fromkeys(e.subject_id for e in spawned if e.subject_id not in ended))


async def _temp_dirs(manifest: HiveManifest, open_ids: set[str]) -> tuple[Path, ...]:
    """Scratch directories named for a lease that is not open; none while keep_scratch is set."""
    if manifest.hive_stand.keep_scratch:
        return ()  # Kept on purpose: the operator asked for scratch to stay.
    base = manifest.resolve_path(manifest.hive_stand.scratch_root)
    # Blocking directory listing, off the loop.
    found = await asyncio.to_thread(_children, base)
    return tuple(path for path in found if path.name not in open_ids and _is_lease_id(path.name))


def _children(base: Path) -> list[Path]:
    """Every directory directly under `base`, sorted; none when it does not exist."""
    return sorted(path for path in base.iterdir() if path.is_dir()) if base.is_dir() else []


def _is_lease_id(name: str) -> bool:
    """Whether `name` parses as a lease id: never `..`, a separator or an absolute path."""
    try:
        parse_id(name, IdKind.LEASE)
    except InvalidIdError:
        return False
    return True


async def _resident(
    sources: ScopeSources, cells: tuple[tuple[str, VirtualCellRecord], ...]
) -> tuple[tuple[CellId, str], ...]:
    """Every resident blob on every Cell the labels or the trail name."""
    known = {record.cell_id for _, record in cells}
    for kind in CELL_KINDS:
        events = await sources.trail.query(TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT))
        known.update(CellId(event.subject_id) for event in events)
    resident: list[tuple[CellId, str]] = []
    for cell_id in sorted(known):
        resident.extend((cell_id, row.sha256) for row in await sources.basket.resident(cell_id))
    return tuple(resident)
