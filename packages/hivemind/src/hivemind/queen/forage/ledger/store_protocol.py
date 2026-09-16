"""Define LedgerStore: the Forage ledger's persistence protocol.

Appendix C of the coding rules lists "Capacity, grants, hosting decisions, snapshots" under the
Forage ledger row, with "SQLite" as the store and "Yes" under "survives a Queen crash"; the same
row's own "Recovery" column reads "Reconciled against fresh capacity reports on Requeening",
which is why this protocol persists every Cell's latest capacity and every Warden's usage report
too, not only grants -- a restart reconciles what it can from fresh reports, but a live grant
itself (a lease a Warden is already spending against) has to survive the gap in between. This
module fixes the one seam both implementations
(`hivemind.queen.forage.ledger.store_memory.InMemoryLedgerStore`,
`hivemind.queen.forage.ledger.store_sqlite.SqliteLedgerStore`) must honour, following the same
Protocol-plus-two-implementations shape `hivemind.memory.store.protocol.MemoryStore` uses for the
memory tables.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Implemented by `hivemind.queen.forage.ledger.store_memory` and `.store_sqlite`;
    used by `hivemind.queen.forage.ledger.book.ForageLedger` to write through every mutation and to
    restore its own in-memory state on start. Calls into `hivemind.forage` (ForageCapacity,
    ForageGrant, RoyalReserve), `hivemind.queen.forage.ledger.model` (LocalPoolReport) and waggle
    only.

Key invariants:
    - Every `put_*` is an upsert keyed by the id named in its own signature: a second call with the
      same key replaces the first, matching a grant's own revision-replaces-revision contract
      (`waggle.messages.forage.grants.GrantIssued`'s own docstring) and a Cell's or Warden's latest
      report replacing whatever it last reported.
    - `delete_grant` is idempotent: deleting an id that is not stored is a no-op, not an error,
      matching `hivemind.memory.store.protocol.MemoryStore.remove_pin`'s own contract.
    - `get_reserve`/`list_capacities`/`list_local_reports`/`list_grants` never raise for an empty
      store; they return the store's own defaults or empty collections.

See Also:
    - .claude/codingrules.md Appendix C, "Capacity, grants, hosting decisions, snapshots" row, for
      the durability contract this protocol implements.
    - hivemind.memory.store.protocol for MemoryStore, the pattern this module follows.
    - hivemind.queen.forage.ledger.book for ForageLedger, this protocol's one caller.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.forage import ForageCapacity, ForageGrant, RoyalReserve
from hivemind.queen.forage.ledger.model import LocalPoolReport
from waggle.ids import CellId, GrantId

__all__ = ["LedgerStore"]


class LedgerStore(Protocol):
    """Persist the ledger's own state: capacities, local reports, grants and the reserve.

    Implementations (`InMemoryLedgerStore`, `SqliteLedgerStore`) must be safe to call
    concurrently.
    """

    async def put_capacity(self, cell_id: CellId, capacity: ForageCapacity) -> None:
        """Upsert `cell_id`'s latest reported capacity.

        Args:
            cell_id: The Cell this capacity belongs to.
            capacity: The capacity report to store, replacing any earlier one for this Cell.
        """
        ...

    async def list_capacities(self) -> tuple[tuple[CellId, ForageCapacity], ...]:
        """Return every stored `(cell_id, capacity)` pair, in no particular order."""
        ...

    async def put_local_report(self, report: LocalPoolReport) -> None:
        """Upsert a Warden's own local-pool usage report, keyed by `report.warden_id`.

        Args:
            report: The report to store, replacing any earlier one from the same Warden.
        """
        ...

    async def list_local_reports(self) -> tuple[LocalPoolReport, ...]:
        """Return every stored local-pool report, in no particular order."""
        ...

    async def put_grant(self, grant: ForageGrant) -> None:
        """Upsert `grant`, keyed by `grant.id`.

        Args:
            grant: The grant to store, replacing any earlier revision under the same id.
        """
        ...

    async def delete_grant(self, grant_id: GrantId) -> None:
        """Remove the grant stored under `grant_id`.

        Idempotent: removing an unknown id is a no-op, not an error.

        Args:
            grant_id: The grant to remove.
        """
        ...

    async def list_grants(self) -> tuple[ForageGrant, ...]:
        """Return every stored grant, in no particular order."""
        ...

    async def put_reserve(self, reserve: RoyalReserve) -> None:
        """Replace the stored Royal Reserve.

        Args:
            reserve: The reserve to store.
        """
        ...

    async def get_reserve(self) -> RoyalReserve | None:
        """Return the stored Royal Reserve, or None when nothing has been stored yet."""
        ...
