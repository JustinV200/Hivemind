"""Define Note: the one thing a bee writes to memory directly, bounded per author.

A Note is a short, bee-written observation that belongs in hot state alongside tasks, Alarms and
questions (codingrules section 8.9: "short notes" are part of hot state, "always loaded, bounded").
It is the only memory-tier row a bee writes on its own initiative rather than through a supervised
transition (a checkpoint, an episode record); the bound comes from `MAX_NOTES_PER_AUTHOR`, enforced
by the store itself (`hivemind.memory.store.sqlite.SqliteMemoryStore` and `.memory.
InMemoryMemoryStore`), which evicts the oldest note for that author once a new one would exceed it
-- documented there as the module's third retention duty alongside `remove_pin` and
`purge_episodes_before`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `add_note` is called by a Worker's or
    Warden's note-writing tool. Calls into hivemind.cell (HoneyClearance), hivemind.memory.context
    (MemoryContext), hivemind.memory.errors (NoteTooLongError) and hivemind.pheromone (MemoryEvent)
    and waggle only.

Key invariants:
    - Note.text has no `Field(max_length=...)` of its own; the cap is enforced entirely by
      `_text_within_bound` below, which raises `NoteTooLongError` directly rather than a bare
      `ValueError`. Pydantic only intercepts `ValueError`/`TypeError`/`AssertionError` from a
      validator, so a `HiveMindError` subclass raised here propagates unwrapped: constructing an
      over-length `Note` raises `NoteTooLongError` itself, not a generic `pydantic.
      ValidationError` a caller would have to inspect to identify.
    - Note.id is an EventId-shaped ULID minted by `waggle.ids.new_event_id`; no dedicated `NoteId`
      IdKind exists this phase.

See Also:
    - .claude/codingrules.md section 8.9 for notes as a bounded, always-loaded part of hot state.
    - .claude/codingrules.md section 12 for the same-transaction rule add_note relies on its store
      to uphold.
    - hivemind.memory.errors for NoteTooLongError, the error `_text_within_bound` raises.
    - hivemind.memory.store.sqlite and .memory for MAX_NOTES_PER_AUTHOR's enforcement (eviction).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hivemind.cell import HoneyClearance
from hivemind.memory.context import MemoryContext
from hivemind.memory.errors import NoteTooLongError
from hivemind.pheromone import MemoryEvent
from waggle.ids import new_event_id
from waggle.messages.base import EventIdField, UtcDatetime

MAX_NOTE_CHARS = 1_000  # A short note: a sentence or two, never a report.
MAX_NOTE_AUTHOR_CHARS = 128  # A bee id or a role name.
MAX_NOTES_PER_AUTHOR = 50  # Enforced by the store, which evicts the oldest past this.

__all__ = ["MAX_NOTES_PER_AUTHOR", "MAX_NOTE_CHARS", "Note", "add_note"]


class Note(BaseModel):
    """A short, bee-written note in hot state: the only memory tier a bee writes directly.

    A store enforces `MAX_NOTES_PER_AUTHOR` by evicting the oldest note for `author` once a new
    one would exceed it (documented on `hivemind.memory.store.sqlite` and `.memory`); nothing in
    this model itself limits how many Notes exist per author.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventIdField = Field(
        description="This note's own id: an EventId-shaped ULID; no NoteId kind exists this phase."
    )
    author: str = Field(
        max_length=MAX_NOTE_AUTHOR_CHARS, description="Who wrote it: a bee id or a role name."
    )
    text: str = Field(
        min_length=1, description="The note itself; see _text_within_bound for its cap."
    )
    clearance: HoneyClearance = Field(description="This note's data-sensitivity label.")
    written_at: UtcDatetime = Field(description="When it was written.")

    @field_validator("text")
    @classmethod
    def _text_within_bound(cls, value: str) -> str:
        """Raise NoteTooLongError, not a bare ValueError, once `value` exceeds MAX_NOTE_CHARS.

        See the module docstring's "Key invariants" for why this deliberately is not a
        `Field(max_length=...)` constraint: pydantic would wrap a plain `ValueError` raised here
        into a generic `ValidationError`, but a `HiveMindError` subclass (this one) is not one of
        the three types pydantic intercepts from a validator, so it propagates unchanged.
        """
        if len(value) > MAX_NOTE_CHARS:
            raise NoteTooLongError(len(value), MAX_NOTE_CHARS)
        return value


async def add_note(note: Note, ctx: MemoryContext) -> None:
    """Write `note` and its `memory.note` trail event, atomically.

    Args:
        note: The note to add; its id must be new to the store.
        ctx: The store, identity and clock to write with.

    Returns:
        None, once the note and its event are durably recorded together (and any note the store
        evicts to stay within MAX_NOTES_PER_AUTHOR has also been removed).
    """
    event = MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind="memory.note",
        subject_id=note.id,
        payload={"chars": len(note.text)},
    )
    await ctx.store.add_note(note, event)
