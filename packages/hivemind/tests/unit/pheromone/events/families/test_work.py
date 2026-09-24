"""Tests for hivemind.pheromone.events.families.work: the cell, task, alarm and worker families.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/events/families/work.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.events.families.work for the module under test.
    - test_codec.py beside this module for the tests every family shares.
"""

from __future__ import annotations

from hivemind.pheromone.events.families import AlarmEvent, CellEvent, TaskEvent, WorkerEvent


def test_worker_event_kinds_cover_every_worker_state_transition() -> None:
    # roadmap step 3.15 (worker runtime): one kind per WorkerState transition it drives.
    assert {
        "worker.spawned",
        "worker.started",
        "worker.handing_off",
        "worker.paused",
        "worker.resumed",
        "worker.done",
        "worker.failed",
        "worker.killed",
    } == WorkerEvent.KINDS


def test_alarm_event_kinds_are_the_four_steps_of_the_chain() -> None:
    # Appendix C's Alarm row: raised, handled at a level, escalated up, resolved -- the human's
    # own acknowledgement of an Alarm that reached them (roadmap step 10.5) resolves it too.
    assert {"alarm.raised", "alarm.handled", "alarm.escalated", "alarm.resolved"} == (
        AlarmEvent.KINDS
    )


def test_task_event_kinds_hold_a_blocked_and_answered_pair_for_questions() -> None:
    assert {"task.blocked", "task.answered", "task.question_withdrawn"} <= TaskEvent.KINDS


def test_cell_event_kinds_record_an_isolation_and_its_lifting() -> None:
    # Roadmap step 10.6a (ADR-0035): only the Queen isolates, and only the human lifts it.
    assert {"cell.isolated", "cell.isolation_lifted"} <= CellEvent.KINDS
