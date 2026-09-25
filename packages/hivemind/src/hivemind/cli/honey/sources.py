"""Implement the Honey browser's wax and Bee Bread sources over the Hive's memory store.

The Honey browser (`hivemind.honey_store.browse`) shows two folders that are not Honey rows: a
Cell's live Cell Wax (the Queen's standing, WRITTEN cautions about that Cell) and recent Bee Bread
(the warm memory tier). Both live in `hivemind.memory`, which the Honey Store may not import, so
the browser asks for them through two Protocols; this module is the composition root's adapter
for each, over the one `MemoryStore` the Hive's database already holds. Each adapter only maps
memory's own records into the browser's neutral `WaxNote`/`BeeBreadNote` shapes: the memory store
applies the reader's clearance allowance itself, and the browser rechecks every label, scope and
expiry, so nothing here decides what a reader may see.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `honey` command group. Built by
    `hivemind.cli.honey.browse` over `hivemind.cli.stores.open_memory`'s store. Calls into
    `hivemind.memory` and `hivemind.honey_store.browse` only.

Key invariants:
    - `MemoryWaxSource` returns WRITTEN notes only: a PROPOSED note is not yet Cell Wax, and a
      CLEARED, EXPIRED or REJECTED one no longer is.
    - A missing Bee Bread entry and one above the reader's allowance both come back as None.

See Also:
    - hivemind.honey_store.browse.sources for the Protocols implemented here.
    - hivemind.memory.store.protocol.MemoryStore for the reads used.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse import BeeBreadNote, WaxNote
from hivemind.memory import (
    BeeBreadEntry,
    BeeBreadEntryNotFoundError,
    CellWax,
    ClearanceError,
    MemoryStore,
    WaxState,
)
from waggle.ids import CellId, EventId
from waggle.messages.cell.wax import WaxSeverity

_LIVE_WAX_STATES = frozenset({WaxState.WRITTEN})  # The one state in which a note is Cell Wax.

__all__ = ["MemoryBeeBreadSource", "MemoryWaxSource"]


class MemoryWaxSource:
    """The browser's LiveWaxSource over the memory store's Cell Wax table."""

    def __init__(self, store: MemoryStore) -> None:
        """Wrap the Hive's memory store.

        Args:
            store: Where Cell Wax notes are kept.
        """
        self._store = store

    async def live_wax(
        self, cell_id: CellId | None, allowance: HoneyClearance
    ) -> tuple[WaxNote, ...]:
        """Return `cell_id`'s WRITTEN notes within `allowance`, newest first.

        Args:
            cell_id: The Cell whose wax to read; None reads every Cell's.
            allowance: The reader's clearance ceiling.

        Returns:
            Each note in the browser's own shape.
        """
        # The memory store's own read on its own thread: milliseconds; already newest first.
        notes = await self._store.list_wax(cell_id, _LIVE_WAX_STATES, allowance)
        return tuple(_wax_note(note) for note in notes)


class MemoryBeeBreadSource:
    """The browser's BeeBreadSource over the memory store's Bee Bread table."""

    def __init__(self, store: MemoryStore) -> None:
        """Wrap the Hive's memory store.

        Args:
            store: Where Bee Bread entries are kept.
        """
        self._store = store

    async def recent(
        self, since: datetime, until: datetime, allowance: HoneyClearance, limit: int
    ) -> tuple[BeeBreadNote, ...]:
        """Return the newest entries in `[since, until]` within `allowance`, newest first.

        Args:
            since: Inclusive lower bound on `created_at`.
            until: Inclusive upper bound on `created_at`.
            allowance: The reader's clearance ceiling.
            limit: The most entries to return.

        Returns:
            At most `limit` entries, in the browser's own shape.
        """
        # The memory store's own time-range read: oldest first, so the newest are at the end.
        entries = await self._store.list_bee_bread_between(since, until, allowance)
        newest = entries[-limit:] if limit > 0 else ()
        return tuple(_bee_bread_note(entry) for entry in reversed(newest))

    async def entry(self, entry_id: str, allowance: HoneyClearance) -> BeeBreadNote | None:
        """Return one entry by id within `allowance`, else None.

        Args:
            entry_id: The entry's own id.
            allowance: The reader's clearance ceiling.

        Returns:
            The entry in the browser's own shape, or None when absent or above `allowance`.
        """
        try:
            # The memory store's own lookup by id: milliseconds.
            entry = await self._store.get_bee_bread_entry(EventId(entry_id), allowance)
        except (BeeBreadEntryNotFoundError, ClearanceError):
            # Missing and forbidden look alike to the browser, on purpose.
            return None
        return _bee_bread_note(entry)


def _wax_note(note: CellWax) -> WaxNote:
    """Map one memory CellWax row into the browser's WaxNote."""
    return WaxNote(
        id=note.id,
        cell_id=note.cell_id,
        # memory's WaxSeverity mirrors the wire's member for member; the value carries across.
        severity=WaxSeverity(note.severity.value),
        text=note.text,
        reason=note.reason,
        origin=note.origin,
        proposer=note.proposer,
        clearance=note.clearance,
        task_id=note.task_id,
        proposed_at=note.proposed_at,
        expires_at=note.expires_at,
    )


def _bee_bread_note(entry: BeeBreadEntry) -> BeeBreadNote:
    """Map one memory BeeBreadEntry into the browser's BeeBreadNote."""
    return BeeBreadNote(
        id=entry.id,
        kind=entry.kind.value,
        task_id=entry.task_id,
        clearance=entry.clearance,
        created_at=entry.created_at,
        text=entry.text,
        payload=entry.payload,
        ref_ids=entry.ref_ids,
    )
