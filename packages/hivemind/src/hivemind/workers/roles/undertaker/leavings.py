"""Define LeavingsStoreRemover: the Undertaker's LeavingsRemover over the real Leavings ledger.

`hivemind.workers.roles.undertaker.role.LeavingsRemover` was written as a Protocol with a no-op
default (`NullLeavingsRemover`) while the Leavings ledger (roadmap step 5.0a) lived on a separate
branch; this module is the adapter that Protocol's own docstring described, now that
`hivemind.cell.leavings.LeavingsStore` has landed. A destroyed Virtual Cell's ledgered paths died
with the Cell itself, so every still-active row for that Cell is marked removed -- one
`cell.leaving_removed` trail event per row, written atomically with the row by the store itself
(`LeavingsStore.mark_removed`'s own contract), never a single rollup event: the ledger's whole
point is that a human can read back exactly which path stopped existing and when.

Only `Undertaker.destroy_virtual` calls this (roadmap step 5.8: "releasing a Real Cell never
touches a ledgered path"), because a Real Cell's Leavings outlive the lease that made them while a
Virtual Cell's do not.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.undertaker`. Built by whichever
    composition root has a live `LeavingsStore` in hand (`hivemind.cli.readback.virtual_offline.
    build_undertaker`, for `hive cells destroy`/`abscond`). Calls into `hivemind.cell` (CellEvent's
    own identity fields), `hivemind.cell.leavings` (LeavingsStore) and `hivemind.pheromone`
    (CellEvent) only. Never imports `hivemind.llm` (the package's own rule).

Key invariants:
    - Idempotent: a Cell whose rows are all already removed returns 0 and writes nothing, because
      `list_leavings` is asked for active rows only (`include_removed=False`).
    - One `cell.leaving_removed` event per row, minted from the injected clock and identity, so
      the trail reads the same whether a human ran `hive cells leavings remove` or the Undertaker
      destroyed the Cell underneath the rows.

See Also:
    - hivemind.workers.roles.undertaker.role for LeavingsRemover, the Protocol this satisfies, and
      NullLeavingsRemover, the default it replaces.
    - hivemind.cell.leavings.store_protocol for LeavingsStore.list_leavings/.mark_removed.
    - hivemind.cli.readback.leavings for the human-driven removal path this mirrors.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.cell import CellIdentity
from hivemind.cell.leavings import Leaving, LeavingsStore
from hivemind.pheromone import CellEvent
from waggle.clock import Clock
from waggle.ids import CellId, new_event_id

# What the `cell.leaving_removed` row records as the reason it was removed: the Cell itself is
# gone, which is a different fact from a human unlinking one path with `hive cells leavings
# remove`, and the ledger keeps them distinguishable.
DEFAULT_REMOVAL_EVENT = "cell_destroyed"

__all__ = ["DEFAULT_REMOVAL_EVENT", "LeavingsStoreRemover"]


class LeavingsStoreRemover:
    """Mark every active Leavings row of a destroyed Virtual Cell removed, one event per row."""

    def __init__(
        self,
        store: LeavingsStore,
        clock: Clock,
        identity: CellIdentity,
        reason: str = DEFAULT_REMOVAL_EVENT,
    ) -> None:
        """Build a remover over `store`, stamping every event with `clock` and `identity`.

        Args:
            store: The live Leavings ledger whose rows this marks removed.
            clock: Source of every `cell.leaving_removed` event id and timestamp.
            identity: The Hive, node and actor stamped on every event written here.
            reason: The `removed_event` string stored on each row; `DEFAULT_REMOVAL_EVENT`
                ("cell_destroyed") distinguishes these rows from a human-driven removal.
        """
        self._store = store
        self._clock = clock
        self._identity = identity
        self._reason = reason

    async def mark_cell_removed(self, cell_id: CellId, at: datetime) -> int:
        """Mark every not-yet-removed Leavings row for `cell_id` removed; see `LeavingsRemover`.

        Args:
            cell_id: The destroyed Virtual Cell.
            at: When the rows are marked removed (the Undertaker's own injected clock reading).

        Returns:
            How many rows this call actually marked removed.
        """
        rows = await self._store.list_leavings(cell_id, include_removed=False)
        for row in rows:
            # No filesystem replay before the call, unlike `hive cells leavings remove`: the path
            # died with the Cell, so there is nothing left to restore or unlink (module docstring).
            await self._store.mark_removed(cell_id, row.path, at, self._event(row, at))
        return len(rows)

    def _event(self, row: Leaving, at: datetime) -> CellEvent:
        """Mint the one `cell.leaving_removed` event that rides with `row`'s own removal."""
        return CellEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=at,
            actor=self._identity.actor,
            kind="cell.leaving_removed",
            subject_id=row.cell_id,
            # The same payload `hive cells leavings remove` writes (hivemind.cli.readback.
            # leavings), plus why: a reader can tell a destroyed Cell from a human's own removal.
            payload={"lease_id": row.lease_id, "path": str(row.path), "reason": self._reason},
        )
