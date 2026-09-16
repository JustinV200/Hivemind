"""Provide InMemoryLedgerStore: the non-durable LedgerStore, for tests and a Queen with no db file.

`InMemoryLedgerStore` implements `hivemind.queen.forage.ledger.store_protocol.LedgerStore` over
plain dicts, mirroring `hivemind.memory.store.memory.InMemoryMemoryStore`'s own shape. It never
survives a process restart (Appendix C's "survives" column is "No" for anything only ever built
this way); `hivemind.queen.forage.ledger.store_sqlite.SqliteLedgerStore` is the durable
implementation Appendix C's Forage ledger row actually asks for.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Constructed by tests (`tests.builders.queen`) and by a composition root that has
    not yet wired a database file. Calls into `hivemind.forage` (ForageCapacity, ForageGrant,
    RoyalReserve), `hivemind.queen.forage.ledger.model` (LocalPoolReport) and
    `hivemind.queen.forage.ledger.store_protocol` (LedgerStore) only.

Key invariants:
    - Every method here mirrors `LedgerStore`'s own contract exactly (upsert semantics, idempotent
      delete, no raise on an empty store); a contract test suite would parametrise over this class
      and `SqliteLedgerStore` together.

See Also:
    - hivemind.memory.store.memory for InMemoryMemoryStore, the pattern this module follows.
    - hivemind.queen.forage.ledger.store_protocol for LedgerStore, the protocol this implements.
"""

from __future__ import annotations

from hivemind.forage import ForageCapacity, ForageGrant, RoyalReserve
from hivemind.queen.forage.ledger.model import LocalPoolReport
from waggle.ids import CellId, GrantId

__all__ = ["InMemoryLedgerStore"]


class InMemoryLedgerStore:
    """The non-durable LedgerStore: three plain dicts, one reserve slot."""

    def __init__(self) -> None:
        """Build an empty store: no capacities, no reports, no grants, no reserve yet."""
        self._capacities: dict[CellId, ForageCapacity] = {}
        self._local_reports: dict[str, LocalPoolReport] = {}
        self._grants: dict[GrantId, ForageGrant] = {}
        self._reserve: RoyalReserve | None = None

    async def put_capacity(self, cell_id: CellId, capacity: ForageCapacity) -> None:
        """Upsert `cell_id`'s latest reported capacity; see `LedgerStore.put_capacity`."""
        self._capacities[cell_id] = capacity

    async def list_capacities(self) -> tuple[tuple[CellId, ForageCapacity], ...]:
        """Return every stored `(cell_id, capacity)` pair; see `LedgerStore.list_capacities`."""
        return tuple(self._capacities.items())

    async def put_local_report(self, report: LocalPoolReport) -> None:
        """Upsert `report`, keyed by its own warden_id; see `LedgerStore.put_local_report`."""
        self._local_reports[report.warden_id] = report

    async def list_local_reports(self) -> tuple[LocalPoolReport, ...]:
        """Return every stored local-pool report; see `LedgerStore.list_local_reports`."""
        return tuple(self._local_reports.values())

    async def put_grant(self, grant: ForageGrant) -> None:
        """Upsert `grant`, keyed by `grant.id`; see `LedgerStore.put_grant`."""
        self._grants[grant.id] = grant

    async def delete_grant(self, grant_id: GrantId) -> None:
        """Remove the grant stored under `grant_id`, if any; see `LedgerStore.delete_grant`."""
        self._grants.pop(grant_id, None)

    async def list_grants(self) -> tuple[ForageGrant, ...]:
        """Return every stored grant; see `LedgerStore.list_grants`."""
        return tuple(self._grants.values())

    async def put_reserve(self, reserve: RoyalReserve) -> None:
        """Replace the stored Royal Reserve; see `LedgerStore.put_reserve`."""
        self._reserve = reserve

    async def get_reserve(self) -> RoyalReserve | None:
        """Return the stored Royal Reserve, or None; see `LedgerStore.get_reserve`."""
        return self._reserve
