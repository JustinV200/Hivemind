"""Tests for hivemind.memory.cell_wax.model: WaxSeverity, CellWax and cap_wax_for_hot_state.

Fits into the Hive:
    Mirrors src/hivemind/memory/cell_wax/model.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.cell_wax.model for the module under test.
"""

from __future__ import annotations

from builders.memory import make_cell_wax

from hivemind.memory.cell_wax import WaxSeverity, cap_wax_for_hot_state
from waggle.clock import FakeClock
from waggle.messages.cell.wax import WaxSeverity as WireWaxSeverity


def test_wax_severity_mirrors_the_wire_enum_member_for_member() -> None:
    assert [member.name for member in WaxSeverity] == [member.name for member in WireWaxSeverity]
    assert [member.value for member in WaxSeverity] == [member.value for member in WireWaxSeverity]


def test_cell_wax_round_trips_through_json() -> None:
    clock = FakeClock()
    wax = make_cell_wax(clock=clock)

    restored = type(wax).model_validate_json(wax.model_dump_json())

    assert restored == wax


def test_cap_wax_for_hot_state_orders_by_severity_then_newest() -> None:
    clock = FakeClock()
    note = make_cell_wax(clock=clock, severity=WaxSeverity.NOTE, proposed_at=clock.now())
    clock.advance(1)
    caution = make_cell_wax(clock=clock, severity=WaxSeverity.CAUTION, proposed_at=clock.now())
    clock.advance(1)
    block = make_cell_wax(clock=clock, severity=WaxSeverity.BLOCK, proposed_at=clock.now())

    ranked = cap_wax_for_hot_state([note, block, caution], cap=10)

    assert [item.id for item in ranked] == [block.id, caution.id, note.id]


def test_cap_wax_for_hot_state_ranks_newest_first_within_the_same_severity() -> None:
    clock = FakeClock()
    older = make_cell_wax(clock=clock, severity=WaxSeverity.CAUTION, proposed_at=clock.now())
    clock.advance(1)
    newer = make_cell_wax(clock=clock, severity=WaxSeverity.CAUTION, proposed_at=clock.now())

    ranked = cap_wax_for_hot_state([older, newer], cap=10)

    assert [item.id for item in ranked] == [newer.id, older.id]


def test_cap_wax_for_hot_state_bounds_to_the_cap() -> None:
    clock = FakeClock()
    items = [make_cell_wax(clock=clock, proposed_at=clock.now()) for _ in range(5)]

    ranked = cap_wax_for_hot_state(items, cap=2)

    assert len(ranked) == 2
