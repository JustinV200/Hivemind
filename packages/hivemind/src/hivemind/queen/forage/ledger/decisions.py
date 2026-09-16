"""Define DecisionBook: the ledger's own copy of every written HostingPlan and set Ceilings.

Appendix C's Forage ledger row names "hosting decisions" alongside capacity and grants as what
survives a Queen crash. `DecisionBook` is that table, split out of
`hivemind.queen.forage.ledger.book.ForageLedger` itself so that class stays within codingrules
5.1's own class-size limit. It holds two small maps -- each Cell's latest
`hivemind.forage.HostingPlan` (`hivemind.queen.forage.hosting.write_hosting_plan`'s own output)
and each Warden's currently set `hivemind.forage.Ceilings`
(`hivemind.queen.forage.ceilings.set_ceilings`'s own output) -- because both are the same shape of
fact: a Queen decision, written once, replaced wholesale on the next revision, read back by
whatever needs to know what is currently in force. Neither write here is itself the trail event
(`forage.plan_written`/`forage.ceilings_set`); this is the ledger half of the same "pure decision,
effectful edge" split every other module in this package follows (codingrules section 8.3).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Owned by `hivemind.queen.forage.ledger.book.ForageLedger` as `ledger.decisions`;
    written by `hivemind.queen.forage.hosting` and `.ceilings`. Calls into `hivemind.forage`
    (Ceilings, HostingPlan), `hivemind.queen.forage.ledger.store_protocol` (LedgerStore) and
    waggle only.

Key invariants:
    - Both maps are keyed by the single id that names "the current one" for their subject (a
      Cell's id for a plan, a Warden's id for ceilings): storing a fresh revision always replaces
      the prior one, never appends to a history the ledger does not keep.

See Also:
    - .claude/codingrules.md Appendix C, "Capacity, grants, hosting decisions, snapshots" row.
    - hivemind.queen.forage.hosting for write_hosting_plan, this book's plan-side writer.
    - hivemind.queen.forage.ceilings for set_ceilings/change_ceilings, this book's ceilings-side
      writer.
"""

from __future__ import annotations

import asyncio

from hivemind.forage import Ceilings, HostingPlan
from hivemind.queen.forage.ledger.store_protocol import LedgerStore
from waggle.ids import CellId, WardenId

__all__ = ["DecisionBook"]


class DecisionBook:
    """Own one Hive's current HostingPlan per Cell and current Ceilings per Warden."""

    def __init__(self, store: LedgerStore | None) -> None:
        """Build an empty book: no plan and no ceilings recorded yet.

        Args:
            store: Where a fresh plan or ceilings is written through; None keeps this book
                in-memory only.
        """
        self._store = store
        self._plans: dict[CellId, HostingPlan] = {}
        self._ceilings: dict[WardenId, Ceilings] = {}
        # How many times record_ceilings has been called for each holder, used to number the
        # wire CeilingsSet's own `revision` field (hivemind.queen.forage.ceilings). In-memory
        # only: Ceilings itself carries no revision field to persist one against (unlike
        # HostingPlan, whose caller sets its own `revision` before ever reaching this class), so a
        # Queen restart resumes numbering from 0 rather than continuing the wire's prior sequence
        # -- a scope simplification flagged in this dispatch's own report.
        self._ceilings_revision: dict[WardenId, int] = {}
        self._lock = asyncio.Lock()

    async def record_plan(self, plan: HostingPlan) -> None:
        """Store a Cell's freshly written HostingPlan, replacing any earlier revision.

        Args:
            plan: The plan to record.
        """
        async with self._lock:
            self._plans[plan.cell_id] = plan
            if self._store is not None:
                await self._store.put_hosting_plan(plan)

    def plan_for(self, cell_id: CellId) -> HostingPlan | None:
        """Return the latest HostingPlan recorded for `cell_id`, or None if none has been yet."""
        return self._plans.get(cell_id)

    async def record_ceilings(self, holder: WardenId, ceilings: Ceilings) -> int:
        """Store a Warden's freshly set Ceilings, replacing any earlier ones.

        Args:
            holder: The Warden these ceilings apply to.
            ceilings: The ceilings to record.

        Returns:
            This holder's new revision number: 0 the first time this is called for `holder`,
            incremented by one on every later call (module docstring's own "Key invariants").
        """
        async with self._lock:
            self._ceilings[holder] = ceilings
            revision = self._ceilings_revision.get(holder, -1) + 1
            self._ceilings_revision[holder] = revision
            if self._store is not None:
                await self._store.put_ceilings(holder, ceilings)
            return revision

    def ceilings_for(self, holder: WardenId) -> Ceilings | None:
        """Return the ceilings currently set for `holder`, or None if none have been set yet."""
        return self._ceilings.get(holder)

    async def restore(self) -> None:
        """Rebuild both maps from `self._store`; a no-op with no store."""
        if self._store is None:
            return
        async with self._lock:
            self._plans = {plan.cell_id: plan for plan in await self._store.list_hosting_plans()}
            self._ceilings = dict(await self._store.list_ceilings())
