"""Tests for hivemind.wardens.watch.deps: the watch settings, schedule and deps bundle.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/deps.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.deps for the module under test.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from builders.watch import StubProbe, make_watch_deps

from hivemind.wardens.watch import WatchDeps, WatchSchedule, WatchSettings
from waggle.clock import FakeClock

_NOW = FakeClock().now()


def test_a_schedule_that_has_never_run_has_a_sample_due_and_no_patrol_due() -> None:
    schedule = WatchSchedule()

    assert schedule.sample_is_due(_NOW) is True
    assert schedule.patrol_is_due(_NOW) is False  # Seeded on the first tick, one interval out.


def test_a_sample_is_due_exactly_at_its_time_and_not_before() -> None:
    schedule = WatchSchedule(next_sample_at=_NOW, next_patrol_at=_NOW)

    assert schedule.sample_is_due(_NOW - timedelta(microseconds=1)) is False
    assert schedule.sample_is_due(_NOW) is True
    assert schedule.patrol_is_due(_NOW - timedelta(microseconds=1)) is False
    assert schedule.patrol_is_due(_NOW) is True


def test_the_settings_are_frozen_and_default_to_no_roots() -> None:
    settings = WatchSettings(60.0, 3_600.0, 600.0, 0.5, 0.95)

    assert settings.roots == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.interval_s = 1.0  # type: ignore[misc]


def test_each_deps_gets_a_schedule_of_its_own() -> None:
    first, second = make_watch_deps(StubProbe()), make_watch_deps(StubProbe())

    first.schedule.is_seeded = True

    assert second.schedule.is_seeded is False
    assert first.bind_session is None  # A probe with no session to lend binds nothing.


def test_deps_are_frozen_but_their_schedule_is_the_one_mutable_part() -> None:
    deps = make_watch_deps(StubProbe())

    with pytest.raises(dataclasses.FrozenInstanceError):
        deps.probe = StubProbe()  # type: ignore[misc]
    deps.schedule.next_sample_at = _NOW

    assert isinstance(deps, WatchDeps) and deps.schedule.next_sample_at == _NOW
