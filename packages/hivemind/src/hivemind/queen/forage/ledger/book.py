"""Define ForageLedger: the Queen's live book of Forage, backed by a LedgerStore.

Roadmap step 4.7: "the live book. Holds every Cell's latest ForageCapacity, ... seats in use and
free per server and provider, spend per grant and per goal, the Royal Reserve, and headroom as
shared totals minus reserve minus the sum of live shared grants. Local pools appear as reported,
not granted... The allocator never hands out local capacity; it only reads it." `ForageLedger`
owns three in-memory tables directly (mirroring `hivemind.forage.map.ForageMap`'s own "own the
live figures in memory, write-through to a store" shape) -- every Cell's latest `ForageCapacity`,
every Warden's `hivemind.queen.forage.ledger.model.LocalPoolReport`, and every live
`hivemind.forage.ForageGrant` -- plus the `hivemind.forage.RoyalReserve` it subtracts first.
Roadmap step 4.8's own four additions (shared-seat capacity and usage, per-goal spend, hosting
plans, ceilings) each get their own small class instead (`ledger.seats`, `ledger.spend`,
`ledger.decisions`), so this class itself stays within codingrules 5.1's own class-size limit;
`headroom()` and `record_spend` are the two places `ForageLedger` still reaches into a sub-book
directly, because both also need `ForageLedger`'s own state (the reserve and the grants) in the
same computation. Every mutating method writes through to its own
`hivemind.queen.forage.ledger.store_protocol.LedgerStore` (Appendix C: the ledger is a SQLite
store that survives a crash), and `restore` rebuilds every in-memory table -- its own three plus
each sub-book's -- from it, for the start of a fresh Queen process. `forget_cell` is the Night Veil
teardown's (codingrules section 12): every row keyed to the Cell or one of its Wardens
(`cell_rows`) leaves memory and the store together.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Built once per Queen (`hivemind.queen.deps.QueenDeps.ledger`); read and written by
    `hivemind.queen.forage.requests`, `.grants`, `.hosting`, `.ceilings` and
    `hivemind.queen.ticks.liveness`. Calls into `hivemind.forage` (ForageCapacity, ForageGrant,
    RoyalReserve, GrantState, is_terminal), this package's own `model` (Headroom, LocalPoolReport),
    `seats` (SeatBook), `spend` (SpendBook), `decisions` (DecisionBook) and `store_protocol`
    (LedgerStore) only.

Key invariants:
    - `_grants` never holds a terminal-state grant (`hivemind.forage.grant_state.is_terminal`):
      `record_grant` removes one the moment its own state becomes REVOKED, the same instant it
      stops counting against `headroom()`.
    - `headroom().sub_bees` and `.shared_seats` are never negative: clamped at zero, since a
      reserve or a burst of grants can in principle outrun a stale capacity reading.
    - Every mutating method writes through to `self._store` before returning, when one is given;
      a ledger built with `store=None` (the default, matching every other `QueenDeps` field's own
      safe fallback) is in-memory only, for a test or a Queen with no database file yet.

See Also:
    - .claude/roadmap.md step 4.7 for this class's own field-by-field description.
    - .claude/roadmap.md step 4.8 for the shared-seat, spend, hosting-plan and ceilings additions.
    - .claude/codingrules.md section 8.10 for "headroom as shared totals minus reserve minus the
      sum of live shared grants" and "local pools appear... as reported, not granted".
    - .claude/codingrules.md Appendix C, "Forage grant" row, for GrantState's own transition table.
    - hivemind.forage.map for ForageMap, the sibling live-table-plus-lock shape this class follows.
    - hivemind.queen.forage.grants for how a grant's GrantState changes reach this ledger.
    - hivemind.queen.forage.ledger.seats, .spend, .decisions for the three sub-books this class
      composes rather than reimplements.
    - hivemind.queen.forage.ledger.recorder for LedgerRecorder, the Fanner-facing feed for
      `ledger.seats` and `record_spend`.
"""

from __future__ import annotations

import asyncio
import types
from collections.abc import Iterable, Mapping

from hivemind.forage import ForageCapacity, ForageGrant, RoyalReserve
from hivemind.forage.grant_state import is_terminal
from hivemind.queen.forage.ledger.decisions import DecisionBook
from hivemind.queen.forage.ledger.model import CellRows, Headroom, LocalPoolReport
from hivemind.queen.forage.ledger.seats import SeatBook
from hivemind.queen.forage.ledger.spend import SpendBook
from hivemind.queen.forage.ledger.store_protocol import LedgerStore
from waggle.errors import InvalidIdError
from waggle.ids import CellId, GrantId, IdKind, TaskId, WardenId, parse_id

__all__ = ["ForageLedger", "cell_rows"]


class ForageLedger:
    """The Queen's live book of Forage: capacities, local reports, live grants, the reserve.

    Owns its own mutable state in place (codingrules section 8.5): `_capacities`,
    `_local_reports` and `_grants` change on every report and every grant edge, and `seats`,
    `spend` and `decisions` (roadmap step 4.8's three sub-books) change the same way through their
    own locks. Guarded by one `asyncio.Lock` around every mutation of this class's own three
    tables, matching `ForageMap`'s own "reads are lock-free, writes are not" split (every read
    method here is a synchronous dict lookup or comprehension, safe under the GIL the same way
    `ForageMap.sources()` is).
    """

    def __init__(
        self, reserve: RoyalReserve | None = None, store: LedgerStore | None = None
    ) -> None:
        """Build a ledger with no reports and no live grants yet.

        Args:
            reserve: The Royal Reserve every headroom computation subtracts first; a default
                `RoyalReserve()` (its own manifest-sensible defaults) when omitted.
            store: Where every mutation is written through; None keeps this ledger in-memory only
                (a test, or a Queen with no database file yet). Shared with every sub-book, so one
                store backs the whole ledger.
        """
        self._reserve = reserve if reserve is not None else RoyalReserve()
        self._store = store
        self._capacities: dict[CellId, ForageCapacity] = {}
        self._local_reports: dict[WardenId, LocalPoolReport] = {}
        self._grants: dict[GrantId, ForageGrant] = {}
        # Roadmap step 4.8's own three sub-books; see the module docstring for why each is its
        # own small class rather than more state and methods on ForageLedger itself.
        self.seats = SeatBook(store)
        self.spend = SpendBook(store)
        self.decisions = DecisionBook(store)
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

    def capacities(self) -> Mapping[CellId, ForageCapacity]:
        """Return every Cell's latest reported capacity, keyed by Cell: a read-only snapshot.

        The Hive Entrance's Forage view reads the whole book this way (ADR-0040: reads go to the
        stores directly); a snapshot, so a report landing meanwhile never changes it under a reader.
        """
        return types.MappingProxyType(dict(self._capacities))

    def headroom(self) -> Headroom:
        """Compute the shared pool's current sub-bee and shared-seat headroom (`_headroom`).

        Returns:
            `Headroom(sub_bees=..., shared_seats=...)`, neither ever negative.
        """
        capacities, grants = self._capacities.values(), self._grants.values()
        return _headroom(capacities, grants, self._reserve, self.seats.total_capacity())

    def rows_about(self, cell_id: CellId, members: frozenset[str]) -> CellRows:
        """Return every row keyed to `cell_id` or its Wardens (`cell_rows`), as the book stands."""
        return cell_rows(cell_id, members, self._local_reports.values(), self._grants.values())

    async def forget_cell(self, cell_id: CellId, members: frozenset[str]) -> int:
        """Forget `rows_about` the Cell, here and in the store; return how many rows went.

        The Night Veil teardown's (codingrules section 12): nothing of the Cell outlives it.
        """
        async with self._lock:
            rows = self.rows_about(cell_id, members)
            removed = _forget(rows, self._capacities, self._local_reports, self._grants)
            removed += self.decisions.forget(rows)
            if self._store is not None:
                await self._store.forget(rows)
        return removed

    async def record_spend(self, grant_id: GrantId, goal_id: TaskId, amount_usd: float) -> None:
        """Add `amount_usd` to a goal's running spend, and to its grant's own `spent` field.

        The two figures roadmap step 4.8 asks the ledger to hold -- "spend per grant and per
        goal" -- share one write: `hivemind.forage.ForageGrant.spent` is already a running total
        per grant (roadmap step 3.12), so this is the one place that keeps both in step, rather
        than `hivemind.queen.forage.ledger.spend.SpendBook` duplicating what the grant already
        carries.

        Args:
            grant_id: The grant this spend was made under; if the ledger holds no live grant with
                this id, only the per-goal total (`self.spend`) is updated (a grant that expired
                or was revoked between the call starting and finishing still leaves its spend
                accounted for).
            goal_id: The goal (a `TaskId`) this spend counts against.
            amount_usd: How much to add, in US dollars; never negative (a correction is a fresh,
                smaller `amount_usd` on a later call, never a negative one).
        """
        await self.spend.record(goal_id, amount_usd)
        async with self._lock:
            grant = self._grants.get(grant_id)
            if grant is not None:
                updated = grant.model_copy(update={"spent": grant.spent + amount_usd})
                self._grants[grant_id] = updated
                if self._store is not None:
                    await self._store.put_grant(updated)

    async def restore(self) -> None:
        """Rebuild every in-memory table from `self._store`; a no-op when no store was given.

        Called once at Queen start (Appendix C: "Reconciled against fresh capacity reports on
        Requeening" -- capacities are naturally superseded by the next report each Cell sends;
        this call restores what a crash would otherwise lose in the meantime, chiefly the live
        grants a Warden is already spending against). Each of the three roadmap step 4.8 sub-books
        restores itself the same way.
        """
        if self._store is not None:
            async with self._lock:
                self._capacities = dict(await self._store.list_capacities())
                self._local_reports = {
                    report.warden_id: report for report in await self._store.list_local_reports()
                }
                self._grants = {grant.id: grant for grant in await self._store.list_grants()}
                stored_reserve = await self._store.get_reserve()
                if stored_reserve is not None:
                    self._reserve = stored_reserve
        await self.seats.restore()
        await self.spend.restore()
        await self.decisions.restore()


def cell_rows(
    cell_id: CellId,
    members: frozenset[str],
    reports: Iterable[LocalPoolReport],
    grants: Iterable[ForageGrant],
) -> CellRows:
    """Return every row keyed to `cell_id` or one of its Wardens (`ForageLedger.rows_about`).

    A Warden is the Cell's when `members` names it, its pool report is about the Cell, or it holds
    a grant on the Cell; a grant is the Cell's when it is on the Cell or held by one of those.
    """
    held = tuple(grants)
    wardens = {WardenId(m) for m in members if _is_kind(m, IdKind.WARDEN)}
    wardens |= {report.warden_id for report in reports if report.cell_id == cell_id}
    wardens |= {grant.holder for grant in held if grant.cell_id == cell_id}
    doomed = [grant for grant in held if grant.cell_id == cell_id or grant.holder in wardens]
    return CellRows(
        cell_id=cell_id,
        wardens=frozenset(wardens),
        grants=frozenset(grant.id for grant in doomed),
        tasks=frozenset(grant.task_id for grant in doomed if grant.task_id is not None),
    )


def _is_kind(value: str, kind: IdKind) -> bool:
    """Return whether `value` is a well-formed id of `kind`."""
    try:
        parse_id(value, kind)
    except InvalidIdError:
        return False  # Another kind of id: a task, a grant, a node.
    return True


def _forget(
    rows: CellRows,
    capacities: dict[CellId, ForageCapacity],
    reports: dict[WardenId, LocalPoolReport],
    grants: dict[GrantId, ForageGrant],
) -> int:
    """Drop `rows` from the book's own three tables; return how many were there."""
    removed = int(capacities.pop(rows.cell_id, None) is not None)
    removed += sum(reports.pop(holder, None) is not None for holder in rows.wardens)
    return removed + sum(grants.pop(grant_id, None) is not None for grant_id in rows.grants)


def _headroom(
    capacities: Iterable[ForageCapacity],
    grants: Iterable[ForageGrant],
    reserve: RoyalReserve,
    seats_total: int,
) -> Headroom:
    """Compute `ForageLedger.headroom` from the book's own tables.

    `sub_bees` is every reported Cell's `max_sub_bees`, less the reserve's headroom-fraction margin
    and its own `seats`, less every live grant's `max_sub_bees`. `shared_seats` (roadmap step 4.8)
    is the declared seat total, less the reserve's own `seats`, less every live grant's own
    `SeatReservation.seats`. Neither is ever negative.
    """
    held = tuple(grants)
    total = sum(capacity.max_sub_bees for capacity in capacities)
    # The same headroom-fraction margin forage.allocate.grant applies to its own sub-bee
    # ceiling, applied here to the shared total before the reserve's seats and every live
    # grant are subtracted, so a burst of individually-valid grants can never collectively
    # outrun what the Cells actually reported.
    after_margin = int(total * (1 - reserve.headroom_fraction))
    committed_sub_bees = sum(g.max_sub_bees for g in held)
    sub_bees = max(0, after_margin - reserve.seats - committed_sub_bees)
    committed_seats = sum(seat.seats for grant in held for seat in grant.seats)
    shared_seats = max(0, seats_total - reserve.seats - committed_seats)
    return Headroom(sub_bees=sub_bees, shared_seats=shared_seats)
