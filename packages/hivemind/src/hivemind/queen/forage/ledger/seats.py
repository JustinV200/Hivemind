"""Define SeatBook: a shared source's declared seat capacity and its live in-flight usage.

Roadmap step 4.8 extends the Forage ledger with "seats in use and free per shared source (server
or hosted provider)". `SeatBook` is that extra table, split out of
`hivemind.queen.forage.ledger.book.ForageLedger` itself so that class stays within codingrules
5.1's own class-size limit (`ForageLedger` already carries capacities, local reports and grants).
Capacity (`set_capacity`) is a declared fact, written through to the store like every other
ledger table; in-flight usage (`mark_started`/`mark_finished`) is fed by the Fanner (the seat
meter every model call passes through, `hivemind.llm.fanner`) through
`hivemind.queen.forage.ledger.recorder.LedgerRecorder`, and is deliberately in-memory only -- a
crash leaves no call genuinely in flight, so restoring a stale count would only ever be wrong
(mirrors codingrules Appendix C's "Provider health | In memory | No").

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Owned by `hivemind.queen.forage.ledger.book.ForageLedger` as `ledger.seats`; read
    by `ForageLedger.headroom` for the shared-seat dimension and by
    `hivemind.queen.forage.requests` for a SHARED_SEATS request. Calls into
    `hivemind.queen.forage.ledger.store_protocol` (LedgerStore) only.

Key invariants:
    - `free` returns None, never a number, when `set_capacity` has never been called for a source:
      "unknown" and "zero free" are different facts, and inventing the latter would let a caller
      wrongly conclude a source has no room at all.
    - `mark_finished` never drives a source's in-flight count below zero.

See Also:
    - .claude/roadmap.md step 4.8 for "seats in use and free per shared source".
    - hivemind.queen.forage.ledger.book for ForageLedger, this class's one owner.
    - hivemind.queen.forage.ledger.recorder for LedgerRecorder, the Fanner-facing caller of
      `mark_started`/`mark_finished`.
"""

from __future__ import annotations

import asyncio

from hivemind.queen.forage.ledger.store_protocol import LedgerStore

__all__ = ["SeatBook"]


class SeatBook:
    """Own one Hive's shared-source seat capacity and live in-flight usage, keyed by source id."""

    def __init__(self, store: LedgerStore | None) -> None:
        """Build an empty book: no declared capacity, nothing in flight yet.

        Args:
            store: Where a capacity declaration is written through; None keeps this book
                in-memory only. In-flight usage is never written through regardless (module
                docstring's own "Key invariants").
        """
        self._store = store
        self._capacities: dict[str, int] = {}
        self._in_use: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def set_capacity(self, source_id: str, seats_total: int) -> None:
        """Record `source_id`'s declared total seats, and persist it.

        Args:
            source_id: The Forage map source (a server or a hosted provider) this capacity is on.
            seats_total: The source's total concurrent-request capacity.
        """
        async with self._lock:
            self._capacities[source_id] = seats_total
            if self._store is not None:
                await self._store.put_seat_capacity(source_id, seats_total)

    async def mark_started(self, source_id: str) -> None:
        """Count one more call in flight on `source_id`; called at the Fanner's own call start."""
        async with self._lock:
            self._in_use[source_id] = self._in_use.get(source_id, 0) + 1

    async def mark_finished(self, source_id: str) -> None:
        """Count one fewer call in flight on `source_id`; called when the Fanner's call returns."""
        async with self._lock:
            self._in_use[source_id] = max(0, self._in_use.get(source_id, 0) - 1)

    def in_use(self, source_id: str) -> int:
        """Return how many calls are in flight on `source_id` right now; 0 if none ever were."""
        return self._in_use.get(source_id, 0)

    def free(self, source_id: str) -> int | None:
        """Return `source_id`'s free seats right now, or None if its capacity is unknown.

        Returns:
            `seats_total - in_use`, never below zero, or None when `set_capacity` has never been
            called for this source (module docstring's own "Key invariants").
        """
        total = self._capacities.get(source_id)
        if total is None:
            return None
        return max(0, total - self.in_use(source_id))

    def total_capacity(self) -> int:
        """Return the sum of every declared source's total seats; 0 if none has been declared."""
        return sum(self._capacities.values())

    async def restore(self) -> None:
        """Rebuild declared capacity from `self._store`; a no-op with no store.

        In-flight usage is never restored (module docstring's own "Key invariants": nothing is
        genuinely in flight across a restart).
        """
        if self._store is None:
            return
        async with self._lock:
            self._capacities = dict(await self._store.list_seat_capacities())
