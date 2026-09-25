"""Provide in-memory LiveWaxSource and BeeBreadSource fakes, for tests and a memory-less browser.

The Honey browser reads a Cell's live Cell Wax (the Queen's standing cautions about that Cell)
and recent Bee Bread (the warm memory tier) through two Protocols, because both live in
`hivemind.memory`, which this package may not import. These fakes implement each Protocol
honestly over a fixed list of items, applying the same clearance allowance and ordering the
memory-backed adapters do (codingrules 14.4: fakes over mocks, shipped beside the Protocol), so a
test can drive every browser folder over a real Honey Store without a memory store, and a
composition root with no memory store to hand can still build a browser whose wax and Bee Bread
folders are simply empty.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Used by `tests/unit/honey_store/browse/**` and by any composition root
    that has no memory store to hand. Calls into this package's `sources` and `hivemind.cell`
    only.

Key invariants:
    - Neither fake ever returns an item above the allowance it was given, matching the memory
      store's own reads.
    - Both return newest first, as the Protocols promise.

See Also:
    - hivemind.honey_store.browse.sources for the Protocols implemented here.
    - hivemind.cli.honey.sources for the memory-backed implementations.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.sources import BeeBreadNote, WaxNote
from waggle.ids import CellId

__all__ = ["FakeBeeBreadSource", "FakeLiveWaxSource"]


class FakeLiveWaxSource:
    """A LiveWaxSource over a fixed set of notes, every one of them treated as WRITTEN."""

    def __init__(self, notes: Iterable[WaxNote] = ()) -> None:
        """Hold the live notes this source serves.

        Args:
            notes: The notes to serve, all taken as WRITTEN; empty for a source with no wax.
        """
        self._notes = tuple(notes)

    async def live_wax(
        self, cell_id: CellId | None, allowance: HoneyClearance
    ) -> tuple[WaxNote, ...]:
        """Return `cell_id`'s notes (every Cell's for None) within `allowance`; see LiveWaxSource.

        Args:
            cell_id: The Cell whose wax to read; None reads every Cell's.
            allowance: The reader's clearance ceiling.

        Returns:
            The matching notes, newest `proposed_at` first.
        """
        matching = (
            note
            for note in self._notes
            if (cell_id is None or note.cell_id == cell_id)
            and note.clearance.rank <= allowance.rank
        )
        return tuple(sorted(matching, key=lambda note: note.proposed_at, reverse=True))


class FakeBeeBreadSource:
    """A BeeBreadSource over a fixed set of entries."""

    def __init__(self, entries: Iterable[BeeBreadNote] = ()) -> None:
        """Hold the entries this source serves.

        Args:
            entries: The entries to serve; empty for a source with no Bee Bread.
        """
        self._entries = tuple(entries)

    async def recent(
        self, since: datetime, until: datetime, allowance: HoneyClearance, limit: int
    ) -> tuple[BeeBreadNote, ...]:
        """Return the newest entries in `[since, until]` within `allowance`; see BeeBreadSource.

        Args:
            since: Inclusive lower bound on `created_at`.
            until: Inclusive upper bound on `created_at`.
            allowance: The reader's clearance ceiling.
            limit: The most entries to return.

        Returns:
            At most `limit` matching entries, newest first.
        """
        matching = (
            entry
            for entry in self._entries
            if since <= entry.created_at <= until and entry.clearance.rank <= allowance.rank
        )
        newest_first = sorted(matching, key=lambda entry: entry.created_at, reverse=True)
        return tuple(newest_first[:limit])

    async def entry(self, entry_id: str, allowance: HoneyClearance) -> BeeBreadNote | None:
        """Return one entry by id within `allowance`, else None; see BeeBreadSource.

        Args:
            entry_id: The entry's own id.
            allowance: The reader's clearance ceiling.

        Returns:
            The entry, or None when absent or above `allowance`.
        """
        for entry in self._entries:
            if entry.id == entry_id and entry.clearance.rank <= allowance.rank:
                return entry
        return None
