"""Tests for hivemind.workers.roles.house_bee.schedule: SweepSchedule's pure timer.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/house_bee/schedule.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.house_bee.schedule for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from hivemind.workers.roles.house_bee.schedule import SweepSchedule
from waggle.clock import FakeClock


def test_next_due_is_now_when_no_sweep_has_ever_run() -> None:
    clock = FakeClock()
    schedule = SweepSchedule(interval_s=3_600.0)

    assert schedule.next_due(None, clock.now()) == clock.now()


def test_next_due_is_last_run_plus_interval() -> None:
    clock = FakeClock()
    schedule = SweepSchedule(interval_s=3_600.0)
    last_run = clock.now()

    assert schedule.next_due(last_run, clock.now()) == last_run + timedelta(seconds=3_600.0)


def test_is_due_false_before_the_interval_elapses() -> None:
    clock = FakeClock()
    schedule = SweepSchedule(interval_s=3_600.0)
    last_run = clock.now()

    assert schedule.is_due(last_run, last_run + timedelta(seconds=1_800.0)) is False


def test_is_due_true_once_the_interval_elapses() -> None:
    clock = FakeClock()
    schedule = SweepSchedule(interval_s=3_600.0)
    last_run = clock.now()

    assert schedule.is_due(last_run, last_run + timedelta(seconds=3_600.0)) is True


def test_is_due_true_past_the_interval() -> None:
    clock = FakeClock()
    schedule = SweepSchedule(interval_s=3_600.0)
    last_run = clock.now()

    assert schedule.is_due(last_run, last_run + timedelta(seconds=7_200.0)) is True


def test_is_due_true_immediately_when_no_sweep_has_ever_run() -> None:
    clock = FakeClock()
    schedule = SweepSchedule(interval_s=3_600.0)

    assert schedule.is_due(None, clock.now()) is True
