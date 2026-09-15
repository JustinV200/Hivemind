"""Tests for hivemind.memory.demote: DemotionReason, should_demote and demote.

Fits into the Hive:
    Mirrors src/hivemind/memory/demote.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.demote for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.memory import make_alarm_summary, make_note, make_pin, make_task_summary

from hivemind.memory.bee_bread import BeeBreadEntryKind
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.demote import DemotionReason, demote, should_demote
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import EventId, new_event_id, new_hive_id, new_node_id, new_task_id

_WINDOW = timedelta(hours=4)  # Matches manifest.schema.supervision.DEFAULT_HOT_WINDOW_S's scale.


def _ctx(clock: FakeClock) -> MemoryContext:
    """Build a fresh MemoryContext over an empty InMemoryMemoryStore."""
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryMemoryStore(trail)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(store=store, identity=identity, clock=clock)


def _note_event(clock: FakeClock, subject_id: EventId) -> MemoryEvent:
    """Build a well-formed `memory.note` event for `add_note`, matching the contract suite's own."""
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="memory.note",
        subject_id=subject_id,
        payload={},
    )


def test_should_demote_returns_none_for_a_pin_no_matter_how_old() -> None:
    clock = FakeClock()
    pin = make_pin(clock=clock)
    far_future = pin.created_at + timedelta(days=365)

    assert should_demote(pin, far_future, frozenset(), frozenset(), _WINDOW) is None


def test_should_demote_task_closed_when_task_no_longer_active() -> None:
    clock = FakeClock()
    task = make_task_summary(clock=clock)

    reason = should_demote(task, task.updated_at, frozenset(), frozenset(), _WINDOW)

    assert reason == DemotionReason.TASK_CLOSED


def test_should_demote_none_when_task_still_active() -> None:
    clock = FakeClock()
    task = make_task_summary(clock=clock)

    reason = should_demote(task, task.updated_at, frozenset({task.id}), frozenset(), _WINDOW)

    assert reason is None


def test_should_demote_alarm_resolved() -> None:
    clock = FakeClock()
    alarm = make_alarm_summary(clock=clock)

    reason = should_demote(alarm, alarm.raised_at, frozenset(), frozenset({alarm.id}), _WINDOW)

    assert reason == DemotionReason.ALARM_RESOLVED


def test_should_demote_aged_out_past_the_window() -> None:
    clock = FakeClock()
    note = make_note(clock=clock)
    past_window = note.written_at + _WINDOW + timedelta(seconds=1)

    reason = should_demote(note, past_window, frozenset(), frozenset(), _WINDOW)

    assert reason == DemotionReason.AGED_OUT


def test_should_demote_none_within_the_window_and_no_other_reason() -> None:
    clock = FakeClock()
    note = make_note(clock=clock)
    still_fresh = note.written_at + _WINDOW - timedelta(seconds=1)

    assert should_demote(note, still_fresh, frozenset(), frozenset(), _WINDOW) is None


def test_should_demote_checks_task_closed_before_alarm_resolved() -> None:
    # An Alarm naming a closed task and also resolved: TASK_CLOSED wins (documented check order).
    clock = FakeClock()
    task_id = new_task_id(clock)
    alarm = make_alarm_summary(clock=clock, task_id=task_id)

    reason = should_demote(alarm, alarm.raised_at, frozenset(), frozenset({alarm.id}), _WINDOW)

    assert reason == DemotionReason.TASK_CLOSED


async def test_demote_archives_a_task_summary_into_bee_bread() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    task = make_task_summary(clock=clock)

    entry = await demote(task, ctx)

    assert entry.kind == BeeBreadEntryKind.TASK_HISTORY
    assert entry.task_id == task.id
    assert entry.text == task.title
    fetched = await ctx.store.get_bee_bread_entry(entry.id, task.clearance)
    assert fetched == entry


async def test_demote_removes_a_note_from_the_store() -> None:
    clock = FakeClock()
    ctx = _ctx(clock)
    note = make_note(clock=clock)
    await ctx.store.add_note(note, _note_event(clock, note.id))

    await demote(note, ctx)

    remaining = await ctx.store.list_notes(note.author, note.clearance, 10)
    assert note.id not in {n.id for n in remaining}
