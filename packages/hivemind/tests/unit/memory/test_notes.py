"""Tests for hivemind.memory.notes: Note's own-error-on-too-long text, and add_note's write path.

Fits into the Hive:
    Mirrors src/hivemind/memory/notes.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.notes for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.memory import make_note
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.errors import NoteTooLongError
from hivemind.memory.notes import MAX_NOTE_CHARS, Note, add_note
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


@dataclass(frozen=True, slots=True)
class _ContextAndTrail:
    """A MemoryContext plus the trail its store records events on, for assertions."""

    ctx: MemoryContext
    trail: PheromoneTrail


def _make_context(clock: FakeClock) -> _ContextAndTrail:
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    ctx = MemoryContext(store=InMemoryMemoryStore(trail), identity=identity, clock=clock)
    return _ContextAndTrail(ctx=ctx, trail=trail)


def test_note_round_trips_through_json() -> None:
    note = make_note()

    restored = Note.model_validate_json(note.model_dump_json())

    assert restored == note


def test_note_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        make_note(text="")


def test_note_over_the_cap_raises_note_too_long_error_directly() -> None:
    # Not wrapped in a pydantic ValidationError: see notes.py's own docstring for why.
    with pytest.raises(NoteTooLongError) as exc_info:
        make_note(text="x" * (MAX_NOTE_CHARS + 1))

    assert exc_info.value.length == MAX_NOTE_CHARS + 1
    assert exc_info.value.limit == MAX_NOTE_CHARS


def test_note_exactly_at_the_cap_is_accepted() -> None:
    note = make_note(text="x" * MAX_NOTE_CHARS)

    assert len(note.text) == MAX_NOTE_CHARS


async def test_add_note_stores_it_and_records_memory_note_on_the_trail() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    note = make_note(clock=clock)

    await add_note(note, context_and_trail.ctx)

    assert await context_and_trail.ctx.store.list_notes(None, HoneyClearance.C2, 10) == (note,)
    events = await context_and_trail.trail.query(TrailQuery(subject_id=note.id))
    assert len(events) == 1
    assert events[0].kind == "memory.note"
