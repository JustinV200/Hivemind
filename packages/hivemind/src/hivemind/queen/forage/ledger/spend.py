"""Define SpendBook: spend recorded per goal, and the headroom it leaves against a cap.

Roadmap step 4.8 extends the Forage ledger with "spend... per goal", deriving "per-goal spend
headroom (the goal's cap minus spend recorded against it)". `SpendBook` is that table, split out
of `hivemind.queen.forage.ledger.book.ForageLedger` itself so that class stays within codingrules
5.1's own class-size limit. It holds only the per-goal running total; a grant's own `spent` field
(`hivemind.forage.ForageGrant.spent`, roadmap step 3.12) already covers "spend... per grant", so
`ForageLedger.record_spend` -- not this class -- is what keeps both figures in step in one write
(see that method's own docstring for why the two cannot be split any further apart than that).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Owned by `hivemind.queen.forage.ledger.book.ForageLedger` as `ledger.spend`; read
    by `hivemind.queen.forage.requests` for a SPEND request. Calls into
    `hivemind.queen.forage.ledger.store_protocol` (LedgerStore) and waggle only.

Key invariants:
    - `headroom` never returns a negative figure: a goal that has overspent its cap (a race
      between two concurrent grants, say) reads as zero free, not negative free.

See Also:
    - .claude/roadmap.md step 4.8 for "spend... per goal" and its derived headroom.
    - hivemind.queen.forage.ledger.book for ForageLedger, this class's one owner, and
      `record_spend` for why a grant's own `spent` field is kept in step alongside this table.
"""

from __future__ import annotations

import asyncio

from hivemind.queen.forage.ledger.store_protocol import LedgerStore
from waggle.ids import TaskId

__all__ = ["SpendBook"]


class SpendBook:
    """Own one Hive's cumulative spend recorded per goal, keyed by goal id (a TaskId)."""

    def __init__(self, store: LedgerStore | None) -> None:
        """Build an empty book: no spend recorded against any goal yet.

        Args:
            store: Where a fresh running total is written through; None keeps this book
                in-memory only.
        """
        self._store = store
        self._by_goal: dict[TaskId, float] = {}
        self._lock = asyncio.Lock()

    async def record(self, goal_id: TaskId, amount_usd: float) -> float:
        """Add `amount_usd` to `goal_id`'s running spend, and persist the new total.

        Args:
            goal_id: The goal this spend counts against.
            amount_usd: How much to add, in US dollars; never negative (a correction is a fresh,
                smaller `amount_usd` on a later call, never a negative one).

        Returns:
            The goal's new running total, so the caller (`ForageLedger.record_spend`) can carry it
            onto the matching grant's own `spent` field without a second lookup.
        """
        async with self._lock:
            total = self._by_goal.get(goal_id, 0.0) + amount_usd
            self._by_goal[goal_id] = total
            if self._store is not None:
                await self._store.put_spend_by_goal(goal_id, total)
            return total

    def for_goal(self, goal_id: TaskId) -> float:
        """Return how much has been recorded against `goal_id` so far; 0.0 if nothing has."""
        return self._by_goal.get(goal_id, 0.0)

    def headroom(self, goal_id: TaskId, cap_usd: float) -> float:
        """Return how much of `goal_id`'s spend cap is still free.

        Args:
            goal_id: The goal to check.
            cap_usd: That goal's spend cap, in US dollars
                (`hivemind.forage.allocate.GoalBudgets.spend_cap_usd`).

        Returns:
            `cap_usd - for_goal(goal_id)`, never negative (module docstring's own "Key
            invariants").
        """
        return max(0.0, cap_usd - self.for_goal(goal_id))

    async def restore(self) -> None:
        """Rebuild every goal's running total from `self._store`; a no-op with no store."""
        if self._store is None:
            return
        async with self._lock:
            self._by_goal = dict(await self._store.list_spend_by_goal())
