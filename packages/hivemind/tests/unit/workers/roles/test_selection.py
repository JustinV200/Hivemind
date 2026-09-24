"""Tests for hivemind.workers.roles.selection.worker_for.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/selection.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.selection for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.workers.roles.drone import Drone
from hivemind.workers.roles.forager import Forager
from hivemind.workers.roles.scout import Scout
from hivemind.workers.roles.selection import UnsupportedWorkerRoleError, worker_for
from waggle.messages.task import WorkerRole


def test_drone_role_builds_a_fresh_drone() -> None:
    worker = worker_for(WorkerRole.DRONE)

    assert isinstance(worker, Drone)
    assert worker.role is WorkerRole.DRONE


def test_forager_role_builds_a_fresh_forager() -> None:
    worker = worker_for(WorkerRole.FORAGER)

    assert isinstance(worker, Forager)
    assert worker.role is WorkerRole.FORAGER


def test_scout_role_builds_a_fresh_scout() -> None:
    worker = worker_for(WorkerRole.SCOUT)

    assert isinstance(worker, Scout)
    assert worker.role is WorkerRole.SCOUT


def test_every_call_returns_a_distinct_instance() -> None:
    first = worker_for(WorkerRole.DRONE)
    second = worker_for(WorkerRole.DRONE)

    assert first is not second


@pytest.mark.parametrize(
    "role", [WorkerRole.GUARD_BEE, WorkerRole.HOUSE_BEE, WorkerRole.UNDERTAKER]
)
def test_a_role_never_spawned_through_a_task_assign_is_refused(role: WorkerRole) -> None:
    with pytest.raises(UnsupportedWorkerRoleError) as exc_info:
        worker_for(role)

    assert exc_info.value.role is role
    assert role.value in str(exc_info.value)
