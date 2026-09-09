"""Tests for hivemind.supervision.supervisor: ChildKind, ChildRef and the Supervisor protocol.

Fits into the Hive:
    Mirrors src/hivemind/supervision/supervisor.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). The Supervisor protocol itself is exercised through FakeSupervisor
    (test_fake.py) and its own contract suite, once one exists for a real implementation; this
    module tests the plain value types defined alongside it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.supervisor for the module under test.
"""

from __future__ import annotations

import dataclasses

from hivemind.supervision.supervisor import ChildKind, ChildRef
from waggle.clock import FakeClock
from waggle.ids import new_task_id


def test_child_ref_is_frozen() -> None:
    ref = ChildRef(id="worker_1", kind=ChildKind.WORKER, task_id=None, state="RUNNING")

    assert dataclasses.is_dataclass(ref)
    try:
        ref.state = "DONE"  # type: ignore[misc]  # The assignment is the test.
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("ChildRef should be frozen")


def test_child_ref_carries_a_task_id_when_given() -> None:
    task_id = new_task_id(FakeClock())

    ref = ChildRef(id="worker_1", kind=ChildKind.WARDEN, task_id=task_id, state="ACTIVE")

    assert ref.task_id == task_id


def test_child_kind_has_exactly_warden_and_worker() -> None:
    assert {member.name for member in ChildKind} == {"WARDEN", "WORKER"}
