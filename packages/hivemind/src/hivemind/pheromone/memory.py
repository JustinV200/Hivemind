"""Provide MemoryPheromoneTrail, an in-process PheromoneTrail for tests and demos.

An in-memory trail is a plain Python list guarded by a lock: no SQL, no file, gone when the
process exits. It exists so a unit test, a `hive doctor` smoke run, or a demo path can exercise
everything above the trail (codingrules 8.7's "fakes live in src/ beside the Protocol") without a
SQLite file, and so `hivemind.pheromone.retention.MemorySegmentPurge` has something to purge for
its own tests. It implements `hivemind.pheromone.trail.PheromoneTrail` exactly like
`hivemind.pheromone.sqlite.SqlitePheromoneTrail` does, which is what the contract suite
(`tests/contracts/test_pheromone_trail_contract.py`) proves.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Used by tests, demos and the Night Veil
    purge's `MemorySegmentPurge` (hivemind.pheromone.retention). Calls into hivemind.pheromone
    (errors, events, trail) and waggle only.

Key invariants:
    - record() and merge_segment() run under the same asyncio.Lock, so two coroutines can never
      both pass the duplicate-id check for the same event before either has appended it.
    - Every read (query, export_segment) returns events sorted by TRAIL_ORDER_KEY, matching
      hivemind.pheromone.sqlite exactly.
    - drop_segment() is the one exception to the "everything under this lock" rule: it is
      synchronous by contract (hivemind.pheromone.retention.MemorySegmentPurge needs a purge it
      can call without an event loop hop), so callers must not run it concurrently with a writer.

See Also:
    - hivemind.pheromone.trail for the PheromoneTrail protocol this class implements.
    - hivemind.pheromone.sqlite for the durable counterpart.
    - hivemind.pheromone.retention for MemorySegmentPurge, drop_segment's one caller.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from operator import attrgetter

from hivemind.pheromone.errors import DuplicateEventError
from hivemind.pheromone.events import PheromoneEvent
from hivemind.pheromone.trail import TRAIL_ORDER_KEY, TrailQuery, TrailSegment
from waggle.clock import Clock
from waggle.ids import NodeId

__all__ = ["MemoryPheromoneTrail"]

# Sorts a list of events the same way hivemind.pheromone.sqlite's `ORDER BY at, node_id, rowid`
# does; built once from TRAIL_ORDER_KEY so the two implementations can never drift apart silently.
# Python's sort is stable, so events that tie on the key keep their recorded (list) order, which is
# exactly what rowid gives the SQLite store.
_trail_sort_key = attrgetter(*TRAIL_ORDER_KEY)


class MemoryPheromoneTrail:
    """An in-process PheromoneTrail: a list of events plus an id set, guarded by one lock."""

    def __init__(self, clock: Clock) -> None:
        """Create an empty trail.

        Args:
            clock: Injected clock; used only to timestamp `export_segment`'s `exported_at`.
        """
        self._clock = clock
        self._events: list[PheromoneEvent] = []
        self._ids: set[str] = set()
        # Serialises record/merge so two coroutines cannot both pass the duplicate-id check on the
        # same event before either has actually appended it, which would otherwise let both
        # succeed for what should be one write and one DuplicateEventError.
        self._lock = asyncio.Lock()

    async def record(self, event: PheromoneEvent) -> None:
        """Append `event`; see `PheromoneTrail.record` for the full contract."""
        async with self._lock:
            self._append_locked(event)

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Return matching events in trail order; see `PheromoneTrail.query` for the contract."""
        async with self._lock:
            # Copy while holding the lock; filtering and sorting below never touch shared state.
            matches = [event for event in self._events if _matches(event, query)]
        matches.sort(key=_trail_sort_key)
        # reverse() after a stable sort, never sort(reverse=True): the latter keeps ties in
        # recorded order while `ORDER BY ... rowid DESC` reverses them, and the contract is that
        # newest_first is the exact reverse of trail order, ties included.
        if query.newest_first:
            matches.reverse()
        return tuple(matches[: query.limit])

    async def export_segment(self, node_id: NodeId, since: datetime | None = None) -> TrailSegment:
        """Export one node's events; see `PheromoneTrail.export_segment` for the contract."""
        async with self._lock:
            events = tuple(
                sorted(
                    (
                        event
                        for event in self._events
                        if event.node_id == node_id and (since is None or event.at >= since)
                    ),
                    key=_trail_sort_key,
                )
            )
        return TrailSegment(node_id=node_id, exported_at=self._clock.now(), events=events)

    async def merge_segment(self, segment: TrailSegment) -> int:
        """Insert every event in `segment` not already known to this trail.

        See `PheromoneTrail.merge_segment` for the full contract.
        """
        async with self._lock:
            inserted = 0
            # Each event whose id is already known is silently skipped, never an error -- that is
            # what makes merging the same segment twice safe (the trail's idempotence guarantee).
            for event in segment.events:
                if event.id in self._ids:
                    continue
                self._append_locked(event)
                inserted += 1
            return inserted

    def _append_locked(self, event: PheromoneEvent) -> None:
        """Append `event`, raising on a known id. Caller must already hold `self._lock`."""
        if event.id in self._ids:
            raise DuplicateEventError(f"event {event.id} is already on the trail.")
        self._events.append(event)
        self._ids.add(event.id)

    def drop_segment(self, node_id: NodeId) -> int:
        """Remove every event recorded under `node_id`, synchronously.

        The Night Veil purge's one Protocol-shaped caller is `hivemind.pheromone.retention.
        MemorySegmentPurge`; this stays synchronous (no `asyncio.Lock`, no thread hop) because it
        is a plain Python list rebuild, not I/O -- teardown happens once the Cell's Warden has
        stopped writing, so nothing else is recording to this trail concurrently.

        Args:
            node_id: The node whose events to remove.

        Returns:
            How many events were removed.
        """
        before = len(self._events)
        self._events = [event for event in self._events if event.node_id != node_id]
        self._ids = {event.id for event in self._events}
        return before - len(self._events)


def _matches(event: PheromoneEvent, query: TrailQuery) -> bool:
    """Return whether `event` satisfies every field `query` has set."""
    if query.since is not None and event.at < query.since:
        return False
    if query.until is not None and event.at > query.until:
        return False
    if query.family is not None and event.family != query.family:
        return False
    if query.kind is not None and event.kind != query.kind:
        return False
    if query.subject_id is not None and event.subject_id != query.subject_id:
        return False
    return not (query.node_id is not None and event.node_id != query.node_id)
