"""Tests for an isolated Cell's two states: the one table, and the trail they are read from.

Roadmap step 10.6a, codingrules Appendix C ("Cell isolation"): OPEN -> ISOLATED is
`cell.isolated`, ISOLATED -> OPEN is `cell.isolation_lifted`, and a Cell's state is whichever of
its two events is newer, so a restarted Queen reads every Cell exactly as she left it.

Fits into the Hive:
    Mirrors src/hivemind/queen/isolation/record.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/codingrules.md Appendix C.
"""

from __future__ import annotations

from builders.isolation import queen_order
from builders.queen import make_queen_deps

from hivemind.queen.isolation import (
    TRANSITIONS,
    IsolationOutcome,
    IsolationState,
    can_transition,
    read_isolation,
)
from hivemind.queen.isolation.record import record_isolated, record_lifted
from waggle.clock import FakeClock


def test_the_table_has_one_row_per_state_and_one_edge_out_of_each() -> None:
    assert set(TRANSITIONS) == set(IsolationState)
    assert can_transition(IsolationState.OPEN, IsolationState.ISOLATED)
    assert can_transition(IsolationState.ISOLATED, IsolationState.OPEN)
    assert not can_transition(IsolationState.OPEN, IsolationState.OPEN)
    assert not can_transition(IsolationState.ISOLATED, IsolationState.ISOLATED)


async def test_a_cells_state_is_its_newest_isolation_event() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    cell_id = link.cell.id
    assert (await read_isolation(deps, cell_id)).state is IsolationState.OPEN

    isolated = await record_isolated(deps, queen_order(cell_id), IsolationOutcome(cell_id=cell_id))
    standing = await read_isolation(deps, cell_id)
    assert standing.state is IsolationState.ISOLATED
    assert standing.isolated is not None and standing.isolated.id == isolated

    clock.advance(1.0)
    await record_lifted(deps, cell_id, {"isolated_event_id": isolated})
    lifted = await read_isolation(deps, cell_id)
    assert lifted.state is IsolationState.OPEN
    assert lifted.isolated is not None and lifted.isolated.id == isolated  # The one it ended.
    await warden_end.close()
