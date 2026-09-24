"""Tests for hivemind.memory.taint.scope: which items one taint covers, by author, task and time.

Fits into the Hive:
    Mirrors src/hivemind/memory/taint/scope.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.taint.scope for TaintScope.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from hivemind.memory.taint import TaintedKind, TaintScope
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_task_id, new_warden_id, new_worker_id, timestamp_of


def test_a_bee_scope_covers_its_own_items_and_its_tasks_from_the_episode_on() -> None:
    clock = FakeClock()
    bee, other = new_worker_id(clock), new_worker_id(clock)
    task = new_task_id(clock)
    episode = new_event_id(clock)
    scope = TaintScope.for_bee(bee, episode, task_ids=(task,))
    at = timestamp_of(episode)

    assert scope.covers(TaintedKind.HANDOFF, bee, None, at)  # Its own Handoff.
    assert scope.covers(TaintedKind.BEE_BREAD, None, task, at)  # Its task's deposit.
    assert not scope.covers(TaintedKind.EPISODE, other, None, at)  # Another bee, another task.
    assert not scope.covers(TaintedKind.HANDOFF, bee, None, at - timedelta(milliseconds=1))


def test_a_cell_scope_covers_every_bee_and_task_that_ran_there() -> None:
    clock = FakeClock()
    warden, worker = new_warden_id(clock), new_worker_id(clock)
    task = new_task_id(clock)
    first_cited = new_event_id(clock)

    scope = TaintScope.for_cell((warden, worker), (task,), first_cited)

    assert scope.authors == frozenset({warden, worker})
    assert scope.covers(TaintedKind.EPISODE, warden, None, timestamp_of(first_cited))


def test_a_scope_reaches_only_its_kinds() -> None:
    clock = FakeClock()
    bee = new_worker_id(clock)
    scope = TaintScope(
        authors=frozenset({bee}), since=clock.now(), kinds=frozenset({TaintedKind.HANDOFF})
    )

    assert scope.covers(TaintedKind.HANDOFF, bee, None, clock.now())
    assert not scope.covers(TaintedKind.EPISODE, bee, None, clock.now())


def test_a_scope_naming_nothing_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least one author or one task"):
        TaintScope(since=FakeClock().now())
