"""Attach every store a Night Veil teardown must clear to the boundary's purge.

Codingrules section 12: nothing of a Night Veil Cell outlives it but its skeleton on the Queen's
trail, and four stores besides the trail hold rows about it: the Queen's memory tables (her
episode records, Handoffs, Bee Bread, notes and Cell Wax about the Cell or its tasks, taint labels
and all), the Brood Chamber (its tasks' words and their questions' and answers'), the Forage
ledger (the Cell's capacity and hosting plan, its Warden's pool report and ceilings, any grant
still held) and the backend's snapshot images; a task still placed on the Cell is cancelled
before the chamber's rows are reduced, since it can never finish now (`BroodChamber.
end_night_veil`). `attach_side_channels` hands the boundary's `NightVeilTeardownPurge` one side
channel per store (`hivemind.pheromone.SideChannels`), each a thin adapter over that store's own
Night Veil method, and the two stores that tie ids to a Cell durably (the chamber's live tasks,
the ledger's reports and grants) as member sources, so a purge by a Queen that never held the
Cell's segment still finds its tasks and its Warden. The
composition root calls it once a Hive's stores exist (`hivemind.cli.compose.hive.build_hive`),
and each offline `hive cells` command that can end a Cell does too.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.night_veil`. Called
    by `hivemind.cli.compose.hive` and `hivemind.cli.readback.virtual`. Calls into
    `hivemind.brood_chamber` (BroodChamber), `hivemind.hive` (BackendRegistry),
    `hivemind.hive.night_veil` (NightVeilBoundary), `hivemind.hive.snapshot` (snapshot_images_for),
    `hivemind.memory` (MemoryStore), `hivemind.pheromone` (SideChannels),
    `hivemind.queen.forage.ledger` (ForageLedger) and waggle only.

Key invariants:
    - Each adapter only forwards to its store's own Night Veil method: what a store removes, and
      why, is documented beside that method, never here.
    - The memory side channel is handed the Cell's own id with its members, since its rows name
      the Cell itself (a Cell Wax row, a note) as often as one of its tasks.

See Also:
    - hivemind.pheromone.retention.purge for SideChannels, SideChannelPurger and MemberSource.
    - .claude/codingrules.md section 12 for the boundary.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.brood_chamber import BroodChamber
from hivemind.hive import BackendRegistry
from hivemind.hive.night_veil import NightVeilBoundary
from hivemind.hive.snapshot import snapshot_images_for
from hivemind.memory import MemoryStore
from hivemind.pheromone import SideChannels
from hivemind.queen.forage.ledger import ForageLedger
from waggle.ids import CellId

__all__ = ["NightVeilStores", "attach_side_channels"]


class NightVeilStores(Protocol):
    """The two Hive stores a Night Veil purge clears besides the ledger (`HiveStores` is one)."""

    @property
    def chamber(self) -> BroodChamber:
        """The Hive's Brood Chamber."""
        ...

    @property
    def memory(self) -> MemoryStore:
        """The Queen's memory tables."""
        ...


def attach_side_channels(
    night_veil: NightVeilBoundary,
    registry: BackendRegistry,
    stores: NightVeilStores,
    ledger: ForageLedger,
) -> None:
    """Attach the memory, Brood Chamber, snapshot-image and ledger side channels to the purge.

    Args:
        night_veil: The boundary whose purge every Night Veil end runs.
        registry: The Virtual side's backends, whose snapshot images a Cell may have left.
        stores: The Hive's Brood Chamber and the Queen's memory tables.
        ledger: The Queen's Forage ledger, restored from the Hive's store.
    """
    tasks, book = _ChamberSide(stores.chamber), _LedgerSide(ledger)
    night_veil.purge.attach(
        SideChannels(
            memory=_MemorySide(stores.memory),
            brood_chamber=tasks,
            snapshot_images=_SnapshotImages(registry),
            forage_ledger=book,
            members=(tasks, book),
        )
    )


class _MemorySide:
    """The Queen's memory tables as a side channel."""

    def __init__(self, memory: MemoryStore) -> None:
        """Purge through `memory.purge_night_veil`."""
        self._memory = memory

    async def purge(self, cell_id: CellId, members: frozenset[str]) -> int:
        """Remove every memory row naming the Cell or one of its members."""
        return await self._memory.purge_night_veil(members | {cell_id})


class _ChamberSide:
    """The Brood Chamber as a side channel and a member source."""

    def __init__(self, chamber: BroodChamber) -> None:
        """Scrub and read through `chamber`'s own Night Veil methods."""
        self._chamber = chamber

    async def purge(self, cell_id: CellId, members: frozenset[str]) -> int:
        """Cancel the Cell's live tasks; reduce every finished Night Veil task to its skeleton."""
        return await self._chamber.end_night_veil(cell_id, members)

    async def members_of(self, cell_id: CellId) -> frozenset[str]:
        """Name the live tasks placed on the Cell and their Wardens."""
        return await self._chamber.bound_to(cell_id)


class _LedgerSide:
    """The Forage ledger as a side channel and a member source."""

    def __init__(self, ledger: ForageLedger) -> None:
        """Forget and read through `ledger`'s own Night Veil methods."""
        self._ledger = ledger

    async def purge(self, cell_id: CellId, members: frozenset[str]) -> int:
        """Forget every row keyed to the Cell or one of its Wardens."""
        return await self._ledger.forget_cell(cell_id, members)

    async def members_of(self, cell_id: CellId) -> frozenset[str]:
        """Name the Cell's Wardens, grants and their tasks, as the book ties them to it."""
        rows = self._ledger.rows_about(cell_id, frozenset())
        return frozenset({*rows.wardens, *rows.grants, *rows.tasks})


class _SnapshotImages:
    """Every registered backend's snapshot images as one side channel."""

    def __init__(self, registry: BackendRegistry) -> None:
        """Remove images through each backend `registry` holds."""
        self._registry = registry

    async def purge(self, cell_id: CellId, members: frozenset[str]) -> int:
        """Remove the Cell's snapshot images from every backend that keeps any."""
        removed = 0
        # By capability and type (`snapshot_images_for`), never by the backend's name.
        for name in self._registry.names():
            images = snapshot_images_for(self._registry.get(name))
            if images is not None:
                removed += await images.purge(cell_id, members)
        return removed
