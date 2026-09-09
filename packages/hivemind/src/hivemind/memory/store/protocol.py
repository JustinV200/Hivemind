"""Define MemoryStore: the memory tables' persistence protocol (pins, notes, handoffs, episodes).

The memory tables (codingrules Appendix C: "Notes, pins, Cell Wax, episode records, Handoffs, Bee
Bread index, watch observations | Memory tables (SQLite) | Yes") are where the durable half of
memory v0 lives. This module fixes the one seam both implementations
(`hivemind.memory.store.memory.InMemoryMemoryStore`, `hivemind.memory.store.sqlite.
SqliteMemoryStore`) must honour: every write takes the `MemoryEvent` to record alongside it and
commits both together, in the same transaction, exactly the way `hivemind.brood_chamber.store.
sqlite.SqliteTaskStore` does for tasks (codingrules section 12). `remove_pin` and
`purge_episodes_before` are this protocol's only two deletion paths named here; `SqliteMemoryStore`
documents a third (evicting a note past `hivemind.memory.notes.MAX_NOTES_PER_AUTHOR`) as an
internal duty of `add_note` rather than a separate method, since nothing above the store ever needs
to trigger it directly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by `hivemind.memory.store.
    memory` and `hivemind.memory.store.sqlite`; used by `hivemind.memory.pins`, `.notes`,
    `.episodes` and `.checkpoint` (through `hivemind.memory.context.MemoryContext.store`) and by
    `hivemind.memory.hot_state.summaries.HotStateSources` implementations that also need to read
    pins and notes. Calls into hivemind.cell (HoneyClearance), hivemind.memory.episodes
    (EpisodeRecord), hivemind.memory.handoff (Handoff), hivemind.memory.notes (Note),
    hivemind.memory.pins (Pin), hivemind.pheromone (MemoryEvent) and waggle only.

Key invariants:
    - Every mutation method's event commits together with the row it describes, or neither
      commits at all (mirrors codingrules Appendix C rule 3 for the Brood Chamber's own TaskStore).
    - `list_pins`, `list_notes` and `list_episodes` each take an `allowance` and return only rows
      whose own `clearance.rank` is at or below it (codingrules section 8.9: clearance filtering).
    - `get_handoff` returns the stored clearance alongside the Handoff itself, so a caller (
      `hivemind.memory.checkpoint.read_handoff`) can refuse an over-clearance read without first
      decoding the whole document.

See Also:
    - .claude/codingrules.md section 12 for the same-transaction rule every implementation follows.
    - .claude/codingrules.md section 8.9 for the clearance-filtering rule every list_* honours.
    - hivemind.memory.store.memory and hivemind.memory.store.sqlite for the two implementations.
    - hivemind.pheromone for MemoryEvent, the event type every write method here takes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.cell import HoneyClearance
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.pheromone import MemoryEvent
from waggle.ids import EventId, TaskId

__all__ = ["MemoryStore"]


class MemoryStore(Protocol):
    """Persist pins, notes, Handoffs and episodes, each mutation atomic with its trail event.

    Implementations (`InMemoryMemoryStore`, `SqliteMemoryStore`) must be safe to call
    concurrently.
    """

    async def add_pin(self, pin: Pin, event: MemoryEvent) -> None:
        """Insert `pin` and record `event`, atomically.

        Args:
            pin: The pin to add; its id must be new to the store.
            event: The accompanying `memory.pinned` trail event.

        Raises:
            hivemind.common.errors.ConflictError: `pin.id` already exists; nothing is written.
        """
        ...

    async def list_pins(self, allowance: HoneyClearance) -> tuple[Pin, ...]:
        """Return every pin whose clearance is within `allowance`, ordered by `(created_at, id)`.

        Args:
            allowance: The reader's clearance ceiling.

        Returns:
            Every matching pin, oldest first.
        """
        ...

    async def remove_pin(self, pin_id: EventId) -> None:
        """Remove the pin with id `pin_id`.

        Idempotent: removing an unknown id is a no-op, not an error. Not accompanied by a trail
        event: no `memory.*` kind names a pin removal this phase.

        Args:
            pin_id: The pin to remove.
        """
        ...

    async def add_note(self, note: Note, event: MemoryEvent) -> None:
        """Insert `note` and record `event`, atomically; evict `note.author`'s oldest if over bound.

        Args:
            note: The note to add; its id must be new to the store.
            event: The accompanying `memory.note` trail event.

        Raises:
            hivemind.common.errors.ConflictError: `note.id` already exists; nothing is written.
        """
        ...

    async def list_notes(
        self, author: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[Note, ...]:
        """Return notes within `allowance`, optionally filtered by `author`, oldest first.

        Args:
            author: When set, only notes written by this author.
            allowance: The reader's clearance ceiling.
            limit: Maximum notes to return.

        Returns:
            At most `limit` matching notes, ordered by `(written_at, id)`.
        """
        ...

    async def put_handoff(
        self, event_id: EventId, handoff: Handoff, task_id: TaskId | None, event: MemoryEvent
    ) -> None:
        """Insert `handoff` keyed by `event_id` and record `event`, atomically.

        Args:
            event_id: The `memory.checkpoint` trail event's own id; the lookup key (`HandoffRef.
                event_id`).
            handoff: The Handoff to store.
            task_id: The task this Handoff concerns, if any.
            event: The accompanying `memory.checkpoint` trail event; `event.id == event_id`.

        Raises:
            hivemind.common.errors.ConflictError: `event_id` already exists; nothing is written.
        """
        ...

    async def get_handoff(self, event_id: EventId) -> tuple[Handoff, HoneyClearance]:
        """Return the stored Handoff and its clearance, by `event_id`.

        Args:
            event_id: The `memory.checkpoint` trail event id (`HandoffRef.event_id`) to look up.

        Returns:
            The Handoff and its own clearance, so a caller can check an allowance without
            decoding the whole document.

        Raises:
            hivemind.memory.errors.HandoffNotFoundError: No Handoff with `event_id` exists.
        """
        ...

    async def put_episode(self, record: EpisodeRecord, event: MemoryEvent) -> None:
        """Insert `record` and record `event`, atomically.

        Args:
            record: The episode to store; its id must be new to the store.
            event: The accompanying `memory.episode` trail event.

        Raises:
            hivemind.common.errors.ConflictError: `record.id` already exists; nothing is written.
        """
        ...

    async def list_episodes(
        self, principal: str | None, allowance: HoneyClearance, limit: int
    ) -> tuple[EpisodeRecord, ...]:
        """Return episodes within `allowance`, optionally filtered by `principal`, newest first.

        Args:
            principal: When set, only episodes recorded for this principal.
            allowance: The reader's clearance ceiling.
            limit: Maximum episodes to return.

        Returns:
            At most `limit` matching episodes, newest first.
        """
        ...

    async def purge_episodes_before(self, cutoff: datetime) -> int:
        """Delete every episode recorded before `cutoff`.

        The store's second deletion path (with `remove_pin`); enforces the retention window
        codingrules section 8.9 gives episode records ("stored... with a retention window").

        Args:
            cutoff: Episodes at or after this time are kept.

        Returns:
            How many episodes were removed.
        """
        ...
