"""Provide InMemoryGuardRequestStore: the in-process GuardRequestStore for tests and demos.

One dict of requests by report id behind one lock: no SQL, no file, gone when the process exits.
It implements `hivemind.queen.guard_requests.protocol.GuardRequestStore` exactly as the durable
`hivemind.queen.guard_requests.sqlite.SqliteGuardRequestStore` does, which the contract suite
(`tests/contracts/test_guard_request_store_contract.py`) proves, and it needs nothing to build, so
`GuardDeps` defaults to one and a `QueenDeps` built without naming a store still takes requests.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Built by `GuardDeps`'s default and by tests. Calls into the
    sub-package's own model and protocol only.

Key invariants:
    - Every method holds the lock for its whole body, so two coroutines never interleave.
    - A decision or a release replaces the stored row with a new value; nothing is mutated.

See Also:
    - hivemind.queen.guard_requests.protocol for the contract this class implements.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from hivemind.queen.guard_requests.model import GuardDecision, GuardRequest, PlacementHold
from hivemind.queen.guard_requests.protocol import DEFAULT_PENDING_PAGE
from waggle.ids import CellId

__all__ = ["InMemoryGuardRequestStore"]


class InMemoryGuardRequestStore:
    """An in-process GuardRequestStore: requests by report id, guarded by one lock."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._requests: dict[str, GuardRequest] = {}
        # Guards the dict: two writers never race on one request.
        self._lock = asyncio.Lock()

    async def file(self, request: GuardRequest) -> bool:
        """Write a fresh request unless its report is filed; see GuardRequestStore.file."""
        async with self._lock:
            if request.id in self._requests:
                return False  # Filed already: the Guard Bee's retry changes nothing.
            self._requests[request.id] = request
            return True

    async def get(self, report_id: str) -> GuardRequest | None:
        """Return one request, or None; see GuardRequestStore.get."""
        async with self._lock:
            return self._requests.get(report_id)

    async def pending(self, limit: int = DEFAULT_PENDING_PAGE) -> tuple[GuardRequest, ...]:
        """Return undecided requests, oldest first; see GuardRequestStore.pending."""
        async with self._lock:
            waiting = [request for request in self._requests.values() if request.is_pending]
        waiting.sort(key=lambda request: (request.filed_at, request.id))
        return tuple(waiting[:limit])

    async def decide(
        self, report_id: str, decision: GuardDecision, hold: PlacementHold | None = None
    ) -> GuardRequest | None:
        """Stamp a decision once; see GuardRequestStore.decide."""
        async with self._lock:
            request = self._requests.get(report_id)
            if request is None or not request.is_pending:
                return request  # Unknown, or decided already: its first decision stands.
            # Built afresh, never model_copy'd: the row's own validator must pass on every value.
            decided = GuardRequest(
                report=request.report, filed_at=request.filed_at, decision=decision, hold=hold
            )
            self._requests[report_id] = decided
            return decided

    async def holds(self) -> tuple[PlacementHold, ...]:
        """Return every active hold, oldest first; see GuardRequestStore.holds."""
        async with self._lock:
            active = [r.hold for r in self._requests.values() if r.hold and r.hold.is_active]
        return tuple(sorted(active, key=lambda hold: (hold.held_at, hold.report_id)))

    async def release_holds(
        self, cell_id: CellId, released_at: datetime
    ) -> tuple[PlacementHold, ...]:
        """Release every active hold on `cell_id`; see GuardRequestStore.release_holds."""
        released: list[PlacementHold] = []
        async with self._lock:
            for report_id, request in self._requests.items():
                hold = request.hold
                if hold is None or not hold.is_active or hold.cell_id != cell_id:
                    continue
                done = hold.model_copy(update={"released_at": released_at})
                self._requests[report_id] = request.model_copy(update={"hold": done})
                released.append(done)
        return tuple(sorted(released, key=lambda hold: (hold.held_at, hold.report_id)))
