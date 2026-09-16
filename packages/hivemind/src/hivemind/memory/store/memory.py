"""Provide InMemoryMemoryStore, an in-process MemoryStore for tests and demos.

An in-memory memory store is five plain Python dicts guarded by a lock: no SQL, no file, gone when
the process exits. It exists so a unit test or a demo path can exercise everything above the store
(codingrules 14.4: "fakes live in src/ beside the Protocol") without a SQLite file. It implements
`hivemind.memory.store.protocol.MemoryStore` exactly like `hivemind.memory.store.sqlite.
SqliteMemoryStore` does, which is what the contract suite
(`tests/contracts/test_memory_store_contract.py`) proves.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by tests and demos. Calls into
    hivemind.cell (HoneyClearance), hivemind.memory (bee_bread, episodes, errors, handoff, notes,
    pins), hivemind.pheromone (PheromoneTrail, MemoryEvent) and waggle only.

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
from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.cell_wax import CellWax, WaxState
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.errors import (
    BeeBreadEntryNotFoundError,
    ClearanceError,
    HandoffNotFoundError,
    WaxNotFoundError,
)
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import MAX_NOTES_PER_AUTHOR, Note
from hivemind.memory.pins import Pin
from hivemind.pheromone import MemoryEvent, PheromoneTrail
from waggle.ids import CellId, EventId, TaskId

__all__ = ["InMemoryMemoryStore"]


class _WaxMemoryStore:
    """The Cell Wax quarter of InMemoryMemoryStore, split out for codingrules 5.1's class size.

    Reads `self._wax`, `self._lock` and `self._trail`, set by `InMemoryMemoryStore.__init__`; this
    class is never instantiated on its own. The three annotations below declare that shared state
    for mypy --strict, which cannot see across a subclass's own `__init__` otherwise.
    """

    _wax: dict[str, CellWax]
    _lock: asyncio.Lock
    _trail: PheromoneTrail

    async def put_wax(self, wax: CellWax, event: MemoryEvent) -> None:
        """Insert `wax` and record `event`; see `MemoryStore.put_wax`."""
        async with self._lock:
            await self._trail.record(event)
            self._wax[wax.id] = wax

    async def get_wax(self, wax_id: str) -> CellWax:
        """Return the note with id `wax_id`; see `MemoryStore.get_wax`."""
        async with self._lock:
            wax = self._wax.get(wax_id)
        if wax is None:
            raise WaxNotFoundError(wax_id)
        return wax

    async def list_wax(
        self, cell_id: CellId | None, states: frozenset[WaxState], allowance: HoneyClearance
    ) -> tuple[CellWax, ...]:
        """Return notes in `states`, within `allowance`; see `MemoryStore.list_wax`."""
        async with self._lock:
            waxes = list(self._wax.values())
        matches = [
            wax
            for wax in waxes
            if (cell_id is None or wax.cell_id == cell_id)
            and wax.state in states
            and wax.clearance.rank <= allowance.rank
        ]
        matches.sort(key=lambda wax: (wax.proposed_at, wax.id), reverse=True)
        return tuple(matches)

    async def update_wax_state(self, wax: CellWax, event: MemoryEvent) -> None:
        """Overwrite the row for `wax.id` and record `event`; see `MemoryStore.update_wax_state`."""
        async with self._lock:
            if wax.id not in self._wax:
                raise WaxNotFoundError(wax.id)
            await self._trail.record(event)
            self._wax[wax.id] = wax


class InMemoryMemoryStore(_WaxMemoryStore):
    """An in-process MemoryStore: 6 dicts (pins/notes/handoffs/episodes/bee_bread/wax), 1 lock.

    Composed with `_WaxMemoryStore` (Cell Wax's own quarter, split out purely for codingrules
    5.1's class-length limit); `InMemoryMemoryStore` itself is the whole class every caller names.
    """

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
        self._bee_bread: dict[str, BeeBreadEntry] = {}
        self._wax: dict[str, CellWax] = {}
        # Guards all six dicts together, matching MemoryTaskStore's own single-lock shape.
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

    async def remove_note(self, note_id: EventId) -> None:
        """Remove the note with id `note_id`; see `MemoryStore.remove_note`."""
        async with self._lock:
            self._notes.pop(note_id, None)  # Idempotent: an unknown id is a no-op, not an error.

    async def add_bee_bread_entry(self, entry: BeeBreadEntry, event: MemoryEvent) -> None:
        """Insert `entry` and record `event`; see `MemoryStore.add_bee_bread_entry`."""
        async with self._lock:
            await self._trail.record(event)
            self._bee_bread[entry.id] = entry

    async def get_bee_bread_entry(
        self, entry_id: EventId, allowance: HoneyClearance
    ) -> BeeBreadEntry:
        """Return the entry with id `entry_id`; see `MemoryStore.get_bee_bread_entry`."""
        async with self._lock:
            entry = self._bee_bread.get(entry_id)
        if entry is None:
            raise BeeBreadEntryNotFoundError(entry_id)
        if entry.clearance.rank > allowance.rank:
            raise ClearanceError(entry.clearance, allowance)
        return entry

    async def list_bee_bread_by_task(
        self, task_id: TaskId, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return entries for `task_id`; see `MemoryStore.list_bee_bread_by_task`."""
        async with self._lock:
            entries = list(self._bee_bread.values())
        matches = [
            entry
            for entry in entries
            if entry.task_id == task_id and entry.clearance.rank <= allowance.rank
        ]
        matches.sort(key=lambda entry: (entry.created_at, entry.id))
        return tuple(matches)

    async def list_bee_bread_between(
        self, start: datetime, end: datetime, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return entries in `[start, end]`; see `MemoryStore.list_bee_bread_between`."""
        async with self._lock:
            entries = list(self._bee_bread.values())
        matches = [
            entry
            for entry in entries
            if start <= entry.created_at <= end and entry.clearance.rank <= allowance.rank
        ]
        matches.sort(key=lambda entry: (entry.created_at, entry.id))
        return tuple(matches)
