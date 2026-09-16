"""Define ForageLedger: the Queen's live book of Forage, backed by a LedgerStore.

Roadmap step 4.7: "the live book. Holds every Cell's latest ForageCapacity, ... seats in use and
free per server and provider, spend per grant and per goal, the Royal Reserve, and headroom as
shared totals minus reserve minus the sum of live shared grants. Local pools appear as reported,
not granted... The allocator never hands out local capacity; it only reads it." `ForageLedger`
owns three in-memory tables (mirroring `hivemind.forage.map.ForageMap`'s own "own the live figures
in memory, write-through to a store" shape) -- every Cell's latest `ForageCapacity`, every Warden's
`hivemind.queen.forage.ledger.model.LocalPoolReport`, and every live `hivemind.forage.ForageGrant`
-- plus the `hivemind.forage.RoyalReserve` it subtracts first. Every mutating method writes through
to its own `hivemind.queen.forage.ledger.store_protocol.LedgerStore` (Appendix C: the ledger is a
SQLite store that survives a crash), and `restore` rebuilds the in-memory tables from it, for the
start of a fresh Queen process. `headroom()` is v1's one live figure
(`hivemind.queen.forage.ledger.model.Headroom`, scoped to the sub-bee dimension -- see that
module's own docstring for why); `hivemind.queen.forage.requests` and
`hivemind.queen.forage.grants` are this ledger's two callers.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Built once per Queen (`hivemind.queen.deps.QueenDeps.ledger`); read and written by
    `hivemind.queen.forage.requests`, `.grants` and `hivemind.queen.ticks.liveness`. Calls into
    `hivemind.forage` (ForageCapacity, ForageGrant, RoyalReserve, GrantState, is_terminal),
    `hivemind.queen.forage.ledger.model` (Headroom, LocalPoolReport) and
    `hivemind.queen.forage.ledger.store_protocol` (LedgerStore) only.

Key invariants:
    - `_grants` never holds a terminal-state grant (`hivemind.forage.grant_state.is_terminal`):
      `record_grant` removes one the moment its own state becomes REVOKED, the same instant it
      stops counting against `headroom()`.
    - `headroom().sub_bees` is never negative: clamped at zero, since a reserve or a burst of
      grants can in principle outrun a stale capacity reading.
    - Every mutating method writes through to `self._store` before returning, when one is given;
      a ledger built with `store=None` (the default, matching every other `QueenDeps` field's own
      safe fallback) is in-memory only, for a test or a Queen with no database file yet.

See Also:
    - .claude/roadmap.md step 4.7 for this class's own field-by-field description.
    - .claude/codingrules.md section 8.10 for "headroom as shared totals minus reserve minus the
      sum of live shared grants" and "local pools appear... as reported, not granted".
    - .claude/codingrules.md Appendix C, "Forage grant" row, for GrantState's own transition table.
    - hivemind.forage.map for ForageMap, the sibling live-table-plus-lock shape this class follows.
    - hivemind.queen.forage.grants for how a grant's GrantState changes reach this ledger.
"""

from __future__ import annotations

import asyncio

from hivemind.forage import ForageCapacity, ForageGrant, RoyalReserve
from hivemind.forage.grant_state import is_terminal
from hivemind.queen.forage.ledger.model import Headroom, LocalPoolReport
from hivemind.queen.forage.ledger.store_protocol import LedgerStore
from waggle.ids import CellId, GrantId, WardenId

__all__ = ["ForageLedger"]


class ForageLedger:
    """The Queen's live book of Forage: capacities, local reports, live grants, the reserve.

    Owns its own mutable state in place (codingrules section 8.5): `_capacities`,
    `_local_reports` and `_grants` change on every report and every grant edge. Guarded by one
    `asyncio.Lock` around every mutation, matching `ForageMap`'s own "reads are lock-free, writes
    are not" split (every read method here is a synchronous dict lookup or comprehension, safe
    under the GIL the same way `ForageMap.sources()` is).
    """

    def __init__(
        self, reserve: RoyalReserve | None = None, store: LedgerStore | None = None
    ) -> None:
        """Build a ledger with no reports and no live grants yet.

        Args:
            reserve: The Royal Reserve every headroom computation subtracts first; a default
                `RoyalReserve()` (its own manifest-sensible defaults) when omitted.
            store: Where every mutation is written through; None keeps this ledger in-memory only
                (a test, or a Queen with no database file yet).
        """
        self._reserve = reserve if reserve is not None else RoyalReserve()
        self._store = store
        self._capacities: dict[CellId, ForageCapacity] = {}
        self._local_reports: dict[WardenId, LocalPoolReport] = {}
        self._grants: dict[GrantId, ForageGrant] = {}
        self._lock = asyncio.Lock()

    @property
    def reserve(self) -> RoyalReserve:
        """The Royal Reserve every headroom computation subtracts first."""
        return self._reserve

    async def set_reserve(self, reserve: RoyalReserve) -> None:
        """Replace the Royal Reserve every headroom computation subtracts, and persist it.

        The composition root calls this whenever the manifest's own `[forage.reserve]` is the
        source of truth for a fresh reserve (normally once, at construction time, alongside
        `__init__`'s own `reserve` argument); `restore` only ever reads back a reserve this call
        already wrote.

        Args:
            reserve: The reserve to use from now on.
        """
        async with self._lock:
            self._reserve = reserve
            if self._store is not None:
                await self._store.put_reserve(reserve)

    async def report_capacity(self, cell_id: CellId, capacity: ForageCapacity) -> None:
        """Record a Cell's latest reported capacity (`forage.capacity_reported`'s own data).

        Args:
            cell_id: The Cell this capacity belongs to.
            capacity: The freshly reported capacity, replacing whatever this Cell last reported.
        """
        async with self._lock:
            self._capacities[cell_id] = capacity
            if self._store is not None:
                await self._store.put_capacity(cell_id, capacity)

    async def report_local_pool(self, report: LocalPoolReport) -> None:
        """Record a Warden's own local-pool usage: reported, never granted (codingrules 8.10).

        Args:
            report: The freshly reported usage, replacing whatever this Warden last reported.
        """
        async with self._lock:
            self._local_reports[report.warden_id] = report
            if self._store is not None:
                await self._store.put_local_report(report)

    async def record_grant(self, grant: ForageGrant) -> None:
        """Upsert `grant`, or drop it the moment its own state is terminal (REVOKED).

        The one write path `hivemind.queen.forage.grants` uses for every grant edge: issue, a
        fresh revision (grow/shrink/top-up), or the transition into REVOKED that frees the
        headroom it held.

        Args:
            grant: The grant's current terms and `GrantState`.
        """
        async with self._lock:
            if is_terminal(grant.state):
                self._grants.pop(grant.id, None)
                if self._store is not None:
                    await self._store.delete_grant(grant.id)
                return
            self._grants[grant.id] = grant
            if self._store is not None:
                await self._store.put_grant(grant)

    def grant(self, grant_id: GrantId) -> ForageGrant | None:
        """Return the live grant stored under `grant_id`, or None."""
        return self._grants.get(grant_id)

    def live_grants(self) -> tuple[ForageGrant, ...]:
        """Return every currently live (non-terminal) grant, in no particular order."""
        return tuple(self._grants.values())

    def grants_for(self, holder: WardenId) -> tuple[ForageGrant, ...]:
        """Return every live grant `holder` currently holds, in no particular order."""
        return tuple(g for g in self._grants.values() if g.holder == holder)

    def capacity_for(self, cell_id: CellId) -> ForageCapacity | None:
        """Return the latest reported capacity for `cell_id`, or None if none has arrived yet."""
        return self._capacities.get(cell_id)

    def headroom(self) -> Headroom:
        """Compute the shared pool's current sub-bee headroom (module docstring's v1 scope).

        Returns:
            `Headroom(sub_bees=...)`: every reported Cell's `max_sub_bees`, less the reserve's
            headroom-fraction margin and its own `seats`, less every live grant's `max_sub_bees`.
            Never negative.
        """
        total = sum(capacity.max_sub_bees for capacity in self._capacities.values())
        # The same headroom-fraction margin forage.allocate.grant applies to its own sub-bee
        # ceiling, applied here to the shared total before the reserve's seats and every live
        # grant are subtracted, so a burst of individually-valid grants can never collectively
        # outrun what the Cells actually reported.
        after_margin = int(total * (1 - self._reserve.headroom_fraction))
        committed = sum(g.max_sub_bees for g in self._grants.values())
        return Headroom(sub_bees=max(0, after_margin - self._reserve.seats - committed))

    async def restore(self) -> None:
        """Rebuild every in-memory table from `self._store`; a no-op when no store was given.

        Called once at Queen start (Appendix C: "Reconciled against fresh capacity reports on
        Requeening" -- capacities are naturally superseded by the next report each Cell sends;
        this call restores what a crash would otherwise lose in the meantime, chiefly the live
        grants a Warden is already spending against).
        """
        if self._store is None:
            return
        async with self._lock:
            self._capacities = dict(await self._store.list_capacities())
            self._local_reports = {
                report.warden_id: report for report in await self._store.list_local_reports()
            }
            self._grants = {grant.id: grant for grant in await self._store.list_grants()}
            stored_reserve = await self._store.get_reserve()
            if stored_reserve is not None:
                self._reserve = stored_reserve
