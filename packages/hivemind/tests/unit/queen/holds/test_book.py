"""Tests for hivemind.queen.holds.book: the Cells the Queen is holding, and where a task resumes.

The dispatcher must be able to ask "is this Cell's node away?" and "where does this re-placed task
resume from?" without knowing who decided. The cases pin the small contracts the swarm gate and the
dispatcher both lean on: a Cell is held or it is not (holding twice keeps the first reason,
releasing an unheld one does nothing), a task with no Cell yet is never held, and a resume hint is
taken exactly once.

Fits into the Hive:
    Mirrors src/hivemind/queen/holds/book.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.holds.book for the module under test.
    - tests.unit.queen.swarm_gate.test_unreachable for the writer of both books.
"""

from __future__ import annotations

from builders.queen import make_queen_deps

from hivemind.queen.holds import HeldCells, ResumeHints
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_task_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance


def test_a_cell_is_held_until_it_is_released() -> None:
    clock = FakeClock()
    held, cell_id = HeldCells(), new_cell_id(clock)
    assert not held.is_held(cell_id)

    held.hold(cell_id, "link_lost")

    assert held.is_held(cell_id)
    assert held.reason(cell_id) == "link_lost"
    assert held.release(cell_id) is True
    assert not held.is_held(cell_id)
    assert held.reason(cell_id) is None


def test_holding_a_cell_twice_keeps_the_first_reason() -> None:
    clock = FakeClock()
    held, cell_id = HeldCells(), new_cell_id(clock)

    held.hold(cell_id, "link_lost")
    held.hold(cell_id, "heartbeats_stopped")

    assert held.reason(cell_id) == "link_lost"


def test_releasing_a_cell_that_is_not_held_does_nothing() -> None:
    held = HeldCells()

    assert held.release(new_cell_id(FakeClock())) is False


def test_a_task_with_no_cell_yet_is_never_held() -> None:
    held = HeldCells()
    held.hold(new_cell_id(FakeClock()), "link_lost")

    assert not held.is_held(None)


def test_one_cell_held_does_not_hold_another() -> None:
    clock = FakeClock()
    held, away, home = HeldCells(), new_cell_id(clock), new_cell_id(clock)

    held.hold(away, "link_lost")

    assert held.is_held(away)
    assert not held.is_held(home)


def _ref(clock: FakeClock) -> HandoffRef:
    """A Handoff reference such as the Queen reads off Bee Bread."""
    return HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=HoneyClearance.C1
    )


def test_a_resume_hint_is_taken_once_and_then_forgotten() -> None:
    clock = FakeClock()
    hints, task_id, ref = ResumeHints(), new_task_id(clock), _ref(clock)

    hints.note(task_id, ref)

    assert hints.take(task_id) == ref
    assert hints.take(task_id) is None


def test_a_task_with_no_hint_starts_fresh() -> None:
    assert ResumeHints().take(new_task_id(FakeClock())) is None


def test_a_newer_hint_replaces_an_older_one_for_the_same_task() -> None:
    clock = FakeClock()
    hints, task_id = ResumeHints(), new_task_id(clock)
    older, newer = _ref(clock), _ref(clock)

    hints.note(task_id, older)
    hints.note(task_id, newer)

    assert hints.take(task_id) == newer


def test_every_queen_starts_with_empty_books() -> None:
    deps, _link, _end = make_queen_deps()

    assert deps.dispatch.held.reasons == {}
    assert deps.dispatch.resumes.refs == {}
