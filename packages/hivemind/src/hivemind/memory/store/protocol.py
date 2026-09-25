"""Define MemoryStore: the memory tables' persistence protocol (pins, notes, handoffs, episodes).

The memory tables (codingrules Appendix C: "Notes, pins, Cell Wax, episode records, Handoffs, Bee
Bread index, watch observations | Memory tables (SQLite) | Yes") are where the durable half of
memory v0 lives. This module fixes the one seam both implementations
(`hivemind.memory.store.memory.InMemoryMemoryStore`, `hivemind.memory.store.sqlite.
SqliteMemoryStore`) must honour: every write takes the `MemoryEvent` to record alongside it and
commits both together, in the same transaction, exactly the way `hivemind.brood_chamber.store.
sqlite.SqliteTaskStore` does for tasks (codingrules section 12). `remove_pin`, `remove_note`
(roadmap step 4.2, for `hivemind.memory.demote.demote`), `purge_episodes_before` and
`purge_night_veil` (the Night Veil teardown's, codingrules section 12: rows about a Night Veil
Cell or its tasks never outlive it) are this protocol's deletion paths named here;
`SqliteMemoryStore` documents one more (evicting a note past
`hivemind.memory.notes.MAX_NOTES_PER_AUTHOR`) as an internal duty of `add_note` rather than a
separate method, since nothing above the store ever needs to trigger it directly. The four
`*_bee_bread_*` methods (roadmap step 4.2) are Bee Bread's (the warm memory tier's) own persistence:
one write, one lookup by id, one by task, one by a time range -- lookup only, no search
(codingrules section 8.9). The four `*_wax` methods (roadmap step 4.2a) are Cell Wax's own
persistence: `put_wax` inserts a fresh proposal, `update_wax_state` overwrites an already-
transitioned row (`hivemind.memory.cell_wax.writes` is the only caller of either, and it always
validates the edge with `hivemind.memory.cell_wax.state.assert_transition` first), `get_wax` and
`list_wax` are the two reads. The three `*_taint*` methods (roadmap step 10.6d, ADR-0035) make the
memory tables a `hivemind.memory.taint.TaintLedger`: `find_taintable` lists what a `TaintScope`
covers that is not already TAINTED, `read_taintable` shows one item as the taint judge sees it, and
`write_taint` replaces one item's label and records its `memory.tainted` or `memory.taint_cleared`
event in the same transaction, checking the label's transition table against the stored label
inside it. Every read that could feed a prompt refuses a TAINTED item: `list_episodes` and the Bee
Bread lists leave it out, `get_bee_bread_entry` raises `TaintedMemoryError`, and `get_handoff`
returns it labelled so `hivemind.memory.checkpoint.read_handoff` can refuse it; and every insert
refuses an item that arrives already labelled.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by `hivemind.memory.store.
    memory` and `hivemind.memory.store.sqlite`; used by `hivemind.memory.pins`, `.notes`,
    `.episodes`, `.checkpoint`, `.demote` and `.bee_bread` (through `hivemind.memory.context.
    MemoryContext.store`) and by `hivemind.memory.hot_state.summaries.HotStateSources`
    implementations that also need to read pins and notes. Calls into hivemind.cell
    (HoneyClearance), hivemind.memory.episodes (EpisodeRecord), hivemind.memory.handoff (Handoff),
    hivemind.memory.notes (Note), hivemind.memory.pins (Pin), hivemind.pheromone (MemoryEvent) and
    waggle only, plus hivemind.memory.bee_bread.entry (BeeBreadEntry) under TYPE_CHECKING (see
    "Key invariants" for why it is not a real import).

Key invariants:
    - Every mutation method's event commits together with the row it describes, or neither
      commits at all (mirrors codingrules Appendix C rule 3 for the Brood Chamber's own TaskStore).
    - `list_pins`, `list_notes`, `list_episodes` and every `list_bee_bread_*`/`get_bee_bread_entry`
      each take an `allowance` and return only rows whose own `clearance.rank` is at or below it
      (codingrules section 8.9: clearance filtering).
    - `get_handoff` returns the stored clearance alongside the Handoff itself, so a caller (
      `hivemind.memory.checkpoint.read_handoff`) can refuse an over-clearance read without first
      decoding the whole document.
    - No list or lookup here ever returns a TAINTED episode record or Bee Bread entry; a TAINTED
      Handoff comes back from `get_handoff` carrying its label, and only `read_taintable` shows
      any item's content regardless of its label (roadmap 10.6d).
    - The `BeeBreadEntry` import below is TYPE_CHECKING-only: `hivemind.memory.bee_bread.index`
      imports `MemoryStore` from this module (also TYPE_CHECKING-only, for the same reason) to
      type `BeeBread.__init__`, so a real, eager import here would close that cycle.
      `from __future__ import annotations` already makes every annotation in this module a string
      at runtime, so this Protocol never needs to resolve the name; only a type checker does.

See Also:
    - .claude/codingrules.md section 12 for the same-transaction rule every implementation follows.
    - .claude/codingrules.md section 8.9 for the clearance-filtering rule every list_* honours.
    - hivemind.memory.store.memory and hivemind.memory.store.sqlite for the two implementations.
    - hivemind.memory.bee_bread for BeeBreadEntry and BeeBread, this protocol's warm-tier consumer.
    - hivemind.pheromone for MemoryEvent, the event type every write method here takes.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from hivemind.cell import HoneyClearance
from hivemind.memory.cell_wax import CellWax, WaxState
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.taint import TaintableItem, TaintMarker, TaintScope, TaintTarget
from hivemind.pheromone import MemoryEvent
from waggle.ids import CellId, EventId, TaskId

if TYPE_CHECKING:
    # Type-checking only: see the module docstring's "Key invariants" for why a real import here
    # would be circular (hivemind.memory.bee_bread.index imports MemoryStore from this module).
    from hivemind.memory.bee_bread.entry import BeeBreadEntry

__all__ = ["MemoryStore"]


class _PinsAndNotesStore(Protocol):
    """A third of MemoryStore (pins and notes), split out only for codingrules 5.1's class size."""

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

    async def remove_note(self, note_id: EventId) -> None:
        """Remove the note with id `note_id`.

        Idempotent: removing an unknown id is a no-op, not an error. Not accompanied by a trail
        event, matching `remove_pin`. Called by `hivemind.memory.demote.demote` once a Note has
        been archived into Bee Bread.

        Args:
            note_id: The note to remove.
        """
        ...


class _HandoffsAndEpisodesStore(Protocol):
    """A third of MemoryStore (Handoffs, episodes), split out for codingrules 5.1's class size."""

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


class _BeeBreadStore(Protocol):
    """A third of MemoryStore (Bee Bread, the warm tier), split out for codingrules 5.1's size."""

    async def add_bee_bread_entry(self, entry: BeeBreadEntry, event: MemoryEvent) -> None:
        """Insert `entry` and record `event`, atomically.

        Args:
            entry: The Bee Bread entry to add; its id must be new to the store.
            event: The accompanying `memory.bee_bread_deposited` trail event.

        Raises:
            hivemind.common.errors.ConflictError: `entry.id` already exists; nothing is written.
        """
        ...

    async def get_bee_bread_entry(
        self, entry_id: EventId, allowance: HoneyClearance
    ) -> BeeBreadEntry:
        """Return the entry with id `entry_id`.

        Args:
            entry_id: The entry's own id.
            allowance: The reader's clearance ceiling.

        Returns:
            The matching BeeBreadEntry.

        Raises:
            hivemind.memory.errors.BeeBreadEntryNotFoundError: No entry with `entry_id` exists.
            hivemind.memory.errors.ClearanceError: The entry's clearance is above `allowance`.
        """
        ...

    async def list_bee_bread_by_task(
        self, task_id: TaskId, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return every entry concerning `task_id`, within `allowance`, oldest first.

        Args:
            task_id: The task to look up entries for.
            allowance: The reader's clearance ceiling.

        Returns:
            Matching entries, ordered by `(created_at, id)`.
        """
        ...

    async def list_bee_bread_between(
        self, start: datetime, end: datetime, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return every entry written in `[start, end]`, within `allowance`, oldest first.

        Args:
            start: Inclusive lower bound.
            end: Inclusive upper bound.
            allowance: The reader's clearance ceiling.

        Returns:
            Matching entries, ordered by `(created_at, id)`.
        """
        ...


class _WaxStore(Protocol):
    """A fifth of MemoryStore (Cell Wax, roadmap 4.2a), split out for codingrules 5.1's size."""

    async def put_wax(self, wax: CellWax, event: MemoryEvent) -> None:
        """Insert `wax` and record `event`, atomically.

        Args:
            wax: The note to add; its id must be new to the store. Always state PROPOSED: only
                `hivemind.memory.cell_wax.writes.propose_wax` calls this.
            event: The accompanying `memory.wax_proposed` trail event.

        Raises:
            hivemind.common.errors.ConflictError: `wax.id` already exists; nothing is written.
        """
        ...

    async def get_wax(self, wax_id: str) -> CellWax:
        """Return the note with id `wax_id`, in whatever state it currently holds.

        Args:
            wax_id: The note's own id.

        Returns:
            The matching CellWax.

        Raises:
            hivemind.memory.errors.WaxNotFoundError: No note with `wax_id` exists.
        """
        ...

    async def list_wax(
        self, cell_id: CellId | None, states: frozenset[WaxState], allowance: HoneyClearance
    ) -> tuple[CellWax, ...]:
        """Return every note in `states`, within `allowance`, newest first.

        Args:
            cell_id: Only notes about this Cell; `None` returns every Cell's (the House Bee
                sweep's own expiry scan, which has no one Cell to filter by).
            states: Only notes currently in one of these states.
            allowance: The reader's clearance ceiling.

        Returns:
            Matching notes, ordered by `(proposed_at, id)` descending (newest first); a caller
            that wants the per-Cell severity-then-recency order re-ranks with
            `hivemind.memory.cell_wax.model.cap_wax_for_hot_state`.
        """
        ...

    async def update_wax_state(self, wax: CellWax, event: MemoryEvent) -> None:
        """Overwrite the stored row for `wax.id` with `wax`, and record `event`, atomically.

        `wax` is always the already-transitioned row (`hivemind.memory.cell_wax.writes` builds it
        via `model_copy` and calls `hivemind.memory.cell_wax.state.assert_transition` first); this
        method trusts the caller and only persists it.

        Args:
            wax: The note's new state, already validated by the caller.
            event: The accompanying `memory.wax_*` trail event for this transition.

        Raises:
            hivemind.memory.errors.WaxNotFoundError: No note with `wax.id` exists yet.
        """
        ...


class _TaintStore(Protocol):
    """A sixth of MemoryStore (the taint label, roadmap 10.6d): the `TaintLedger` it satisfies."""

    async def find_taintable(self, scope: TaintScope) -> tuple[TaintTarget, ...]:
        """Return every Handoff, episode and Bee Bread entry `scope` covers not already TAINTED.

        Args:
            scope: The authors, tasks, kinds and moment a taint covers.

        Returns:
            Their targets, oldest first; empty when the scope covers nothing new.
        """
        ...

    async def read_taintable(self, target: TaintTarget) -> TaintableItem:
        """Return one item's label, clearance and the text a taint judge reviews.

        Args:
            target: A HANDOFF, EPISODE or BEE_BREAD item.

        Returns:
            The item as `hivemind.memory.taint.clear.clear_taint` needs it.

        Raises:
            hivemind.memory.errors.TaintTargetNotFoundError: No such item (or not a kind the
                memory tables hold).
        """
        ...

    async def write_taint(
        self, target: TaintTarget, marker: TaintMarker, event: MemoryEvent
    ) -> None:
        """Replace `target`'s label with `marker` and record `event`, atomically.

        Args:
            target: The item to label.
            marker: Its new label.
            event: The accompanying `memory.tainted` or `memory.taint_cleared` event.

        Raises:
            hivemind.memory.errors.TaintTargetNotFoundError: No such item.
            hivemind.memory.errors.InvalidTaintTransitionError: The stored label cannot move to
                `marker.state` (hivemind.memory.taint.state); nothing is written.
        """
        ...


class _NightVeilStore(Protocol):
    """The Night Veil purge (codingrules section 12): the memory tables' one exception."""

    async def purge_night_veil(self, ids: frozenset[str]) -> int:
        """Remove every memory row naming one of `ids`, with its taint label.

        An episode record, Handoff, Bee Bread entry, note or Cell Wax row names an id as its key
        (principal, task, author, Cell) or anywhere in its stored body. The one deletion path
        beyond the retention and eviction rules above, and only the Night Veil teardown purge
        calls it (`hivemind.memory.store.night_veil`): the rows about a Night Veil Cell or its
        tasks must not outlive the Cell. Pins are never touched. No event is recorded; the purge's
        own `cell.purged` counts what went.

        Args:
            ids: The Night Veil Cell's id and every id that belongs to it (its tasks, Wardens,
                grants, ...); an empty set removes nothing.

        Returns:
            How many rows were removed, across every table.
        """
        ...


class MemoryStore(
    _PinsAndNotesStore,
    _HandoffsAndEpisodesStore,
    _BeeBreadStore,
    _WaxStore,
    _TaintStore,
    _NightVeilStore,
    Protocol,
):
    """Persist pins, notes, Handoffs, episodes, Bee Bread entries and Cell Wax, atomic with events.

    Composed from the six private Protocols above, split only to keep each one under codingrules
    5.1's class-length limit; `MemoryStore` itself is the whole contract every caller and
    implementation (`InMemoryMemoryStore`, `SqliteMemoryStore`) actually names. Implementations
    must be safe to call concurrently. It is also a `hivemind.memory.taint.TaintLedger`.
    """
