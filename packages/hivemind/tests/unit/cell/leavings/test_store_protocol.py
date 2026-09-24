"""Tests for hivemind.cell.leavings.store_protocol: check_leaving_event's own guard.

LeavingsStore's own behavioural contract is covered by
tests/contracts/test_leavings_store_contract.py, parametrised over InMemoryLeavingsStore and
SqliteLeavingsStore. This module covers what belongs to store_protocol.py itself:
check_leaving_event, the guard both implementations call before every write.

Fits into the Hive:
    Mirrors src/hivemind/cell/leavings/store_protocol.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.leavings.store_protocol for the module under test.
    - tests/contracts/test_leavings_store_contract.py for the shared LeavingsStore behaviour.
"""

from __future__ import annotations

import pytest

from hivemind.cell.leavings.store_protocol import check_leaving_event
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import CellEvent, TaskEvent
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_hive_id, new_node_id


def _make_cell_event(clock: FakeClock, subject_id: str, kind: str = "cell.left") -> CellEvent:
    """Build a well-formed CellEvent naming `subject_id`, minting a fresh id and node."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


def test_check_leaving_event_accepts_a_matching_pair() -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    event = _make_cell_event(clock, cell_id)

    check_leaving_event(cell_id, event)  # does not raise


def test_check_leaving_event_rejects_a_mismatched_subject_id() -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    event = _make_cell_event(clock, new_cell_id(clock))

    with pytest.raises(InvariantViolationError):
        check_leaving_event(cell_id, event)


def test_check_leaving_event_rejects_a_mismatched_family() -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    # A TaskEvent whose subject_id equals the cell's own id string (waggle IdKind prefixes are
    # not cross-checked against the field's own domain), so this isolates the family check from
    # the (already covered) subject_id check, mirroring hivemind.brood_chamber.store.protocol's
    # own test_check_task_event_rejects_a_mismatched_family.
    task_event = TaskEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="task.submitted",
        subject_id=cell_id,
        payload={},
    )

    with pytest.raises(InvariantViolationError):
        check_leaving_event(cell_id, task_event)  # type: ignore[arg-type]
