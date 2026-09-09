"""Provide InMemoryMemoryStore, an in-process MemoryStore for tests and demos.

An in-memory memory store is four plain Python dicts guarded by a lock: no SQL, no file, gone when
the process exits. It exists so a unit test or a demo path can exercise everything above the store
(codingrules 14.4: "fakes live in src/ beside the Protocol") without a SQLite file. It implements
`hivemind.memory.store.protocol.MemoryStore` exactly like `hivemind.memory.store.sqlite.
SqliteMemoryStore` does, which is what the contract suite
(`tests/contracts/test_memory_store_contract.py`) proves.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by tests and demos. Calls into
    hivemind.cell (HoneyClearance), hivemind.memory (episodes, errors, handoff, notes, pins),
    hivemind.pheromone (PheromoneTrail, MemoryEvent) and waggle only.

Key invariants:
    - Every mutation records its event on `self._trail` before assigning the dict's new value, so
      a failed `record` (a `DuplicateEventError`, propagated unchanged) leaves the dicts exactly
      as they were before the call, matching `hivemind.brood_chamber.store.memory.
      MemoryTaskStore`'s own shape.
    - `add_note` evicts `note.author`'s oldest note past `MAX_NOTES_PER_AUTHOR`, the same duty
      `hivemind.memory.store.sqlite.SqliteMemoryStore` documents for its own third DELETE.
    - Every method holds `self._lock` for its whole body, so two coroutines can never interleave a
      read with a write, or two writes with each other.

See Also:
    - hivemind.memory.store.protocol for the MemoryStore protocol this class implements.
    - hivemind.memory.store.sqlite for the durable counterpart.
    - hivemind.brood_chamber.store.memory for MemoryTaskStore, the pattern this module mirrors.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.errors import HandoffNotFoundError
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import MAX_NOTES_PER_AUTHOR, Note
from hivemind.memory.pins import Pin
from hivemind.pheromone import MemoryEvent, PheromoneTrail
from waggle.ids import EventId, TaskId

__all__ = ["InMemoryMemoryStore"]


class InMemoryMemoryStore:
    """An in-process MemoryStore: four dicts (pins, notes, handoffs, episodes) behind one lock."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Create an empty store over `trail`.

        Args:
            trail: Where every mutation's event is recorded before the dicts change.
        """
        self._trail = trail
        self._pins: dict[str, Pin] = {}
        self._notes: dict[str, Note] = {}
        self._handoffs: dict[str, tuple[Handoff, TaskId | None]] = {}
        self._episodes: dict[str, EpisodeRecord] = {}
        # Guards all four dicts together, matching MemoryTaskStore's own single-lock shape.
        self._lock = asyncio.Lock()

    async def add_pin(self, pin: Pin, event: MemoryEvent) -> None:
        """Insert `pin` and record `event`; see `MemoryStore.add_pin`."""
        async with self._lock:
            await self._trail.record(event)
            self._pins[pin.id] = pin

    async def list_pins(self, allowance: HoneyClearance) -> tuple[Pin, ...]:
        """Return pins within `allowance`, oldest first; see `MemoryStore.list_pins`."""
        async with self._lock:
            pins = list(self._pins.values())
        matches = [pin for pin in pins if pin.clearance.rank <= allowance.rank]
        matches.sort(key=lambda pin: (pin.created_at, pin.id))
        return tuple(matches)

    async def remove_pin(self, pin_id: EventId) -> None:
        """Remove the pin with id `pin_id`; see `MemoryStore.remove_pin`."""
        async with self._lock:
            self._pins.pop(pin_id, None)  # Idempotent: an unknown id is a no-op, not an error.

    async def add_note(self, note: Note, event: MemoryEvent) -> None:
        """Insert `note`, record `event`, then evict `note.author`'s oldest if over bound."""
        async with self._lock:
            await self._trail.record(event)
            self._notes[note.id] = note
            self._evict_oldest_notes_over_bound(note.author)

    def _evict_oldest_notes_over_bound(self, author: str) -> None:
        """Drop `author`'s oldest notes past MAX_NOTES_PER_AUTHOR. Caller must hold `self._lock`."""
        author_notes = sorted(
            (note for note in self._notes.values() if note.author == author),
            key=lambda note: (note.written_at, note.id),
        )
        # Oldest first (the sort above): drop from the front until back within the bound.
        excess = len(author_notes) - MAX_NOTES_PER_AUTHOR
        for stale in author_notes[: max(excess, 0)]:
            del self._notes[stale.id]

    async def list_notes(
        self, author: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[Note, ...]:
        """Return notes within `allowance`, oldest first; see `MemoryStore.list_notes`."""
        async with self._lock:
            notes = list(self._notes.values())
        matches = [
            note
            for note in notes
            if note.clearance.rank <= allowance.rank and (author is None or note.author == author)
        ]
        matches.sort(key=lambda note: (note.written_at, note.id))
        return tuple(matches[:limit])

    async def put_handoff(
        self, event_id: EventId, handoff: Handoff, task_id: TaskId | None, event: MemoryEvent
    ) -> None:
        """Insert `handoff` keyed by `event_id` and record `event`.

        See `MemoryStore.put_handoff` for the full contract.
        """
        async with self._lock:
            await self._trail.record(event)
            self._handoffs[event_id] = (handoff, task_id)

    async def get_handoff(self, event_id: EventId) -> tuple[Handoff, HoneyClearance]:
        """Return the stored Handoff and its clearance; see `MemoryStore.get_handoff`."""
        async with self._lock:
            entry = self._handoffs.get(event_id)
        if entry is None:
            raise HandoffNotFoundError(event_id)
        handoff, _task_id = entry
        return handoff, handoff.clearance

    async def put_episode(self, record: EpisodeRecord, event: MemoryEvent) -> None:
        """Insert `record` and record `event`; see `MemoryStore.put_episode`."""
        async with self._lock:
            await self._trail.record(event)
            self._episodes[record.id] = record

    async def list_episodes(
        self, principal: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[EpisodeRecord, ...]:
        """Return episodes within `allowance`, newest first; see `MemoryStore.list_episodes`."""
        async with self._lock:
            episodes = list(self._episodes.values())
        matches = [
            record
            for record in episodes
            if record.clearance.rank <= allowance.rank
            and (principal is None or record.principal == principal)
        ]
        matches.sort(key=lambda record: (record.at, record.id), reverse=True)
        return tuple(matches[:limit])

    async def purge_episodes_before(self, cutoff: datetime) -> int:
        """Delete episodes recorded before `cutoff`; see `MemoryStore.purge_episodes_before`."""
        async with self._lock:
            stale_ids = [rid for rid, record in self._episodes.items() if record.at < cutoff]
            for rid in stale_ids:
                del self._episodes[rid]
            return len(stale_ids)
