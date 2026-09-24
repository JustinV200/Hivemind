"""Provide InMemoryGoalRequestStore, the in-process GoalRequestStore for tests and demos.

One dict of requests by id behind one lock: no SQL, no file, gone when the process exits. It
implements `hivemind.queen.intake.protocol.GoalRequestStore` exactly as the durable
`hivemind.queen.intake.sqlite.SqliteGoalRequestStore` does, which the contract suite
(`tests/contracts/test_goal_request_store_contract.py`) proves, so a unit test can exercise the
Queen's whole intake path without a database file (codingrules 14.4: fakes live in src beside
the Protocol).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Built by `tests.builders.queen.make_queen_deps` and any composition root that
    wants no file. Calls into `hivemind.pheromone` (PheromoneTrail, QueenEvent) and the intake
    package's own errors, model and protocol only.

Key invariants:
    - A mutation checks everything first, then records its event, and only after the record
      succeeds does it change the dict: a failed record leaves the store as it was, matching the
      SQLite store's single transaction.
    - Every method holds the lock for its whole body, so two coroutines never interleave.

See Also:
    - hivemind.queen.intake.protocol for the contract this class implements.
    - hivemind.brood_chamber.store.memory for MemoryTaskStore, the pattern this class mirrors.
"""

from __future__ import annotations

import asyncio

from hivemind.pheromone import PheromoneTrail, QueenEvent
from hivemind.queen.intake.errors import GoalRequestExistsError, GoalRequestNotFoundError
from hivemind.queen.intake.model import GoalRequest
from hivemind.queen.intake.protocol import GoalRequestQuery, check_goal_request_event

__all__ = ["InMemoryGoalRequestStore"]


class InMemoryGoalRequestStore:
    """An in-process GoalRequestStore: requests by id, guarded by one lock."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Create an empty store over `trail`.

        Args:
            trail: Where every write's event is recorded before the dict changes.
        """
        self._trail = trail
        self._requests: dict[str, GoalRequest] = {}
        # Guards the dict: a reader never sees a row whose event is not yet recorded, and two
        # writers never race on the same id.
        self._lock = asyncio.Lock()

    async def insert(self, request: GoalRequest, event: QueenEvent) -> None:
        """Insert a fresh request with its event; see GoalRequestStore.insert."""
        check_goal_request_event(request, event)
        async with self._lock:
            if request.id in self._requests:
                raise GoalRequestExistsError(request.id)
            await self._trail.record(event)
            self._requests[request.id] = request

    async def update(self, request: GoalRequest, event: QueenEvent) -> None:
        """Replace the stored request and record `event`; see GoalRequestStore.update."""
        check_goal_request_event(request, event)
        async with self._lock:
            if request.id not in self._requests:
                raise GoalRequestNotFoundError(request.id)
            await self._trail.record(event)
            self._requests[request.id] = request

    async def get(self, request_id: str) -> GoalRequest:
        """Return the stored request; see GoalRequestStore.get."""
        async with self._lock:
            request = self._requests.get(request_id)
        if request is None:
            raise GoalRequestNotFoundError(request_id)
        return request

    async def list_requests(self, query: GoalRequestQuery) -> tuple[GoalRequest, ...]:
        """Return every matching request, oldest first; see GoalRequestStore.list_requests."""
        async with self._lock:
            # Copied under the lock; filtering and sorting below never touch shared state.
            requests = list(self._requests.values())
        matches = [request for request in requests if _matches(request, query)]
        matches.sort(key=lambda request: (request.received_at, request.id))
        return tuple(matches[: query.limit])


def _matches(request: GoalRequest, query: GoalRequestQuery) -> bool:
    """Return whether `request` satisfies every field `query` has set."""
    if query.state is not None and request.state is not query.state:
        return False
    if query.device_id is not None and request.device_id != query.device_id:
        return False
    return not (query.unfinished and request.finished_at is not None)
