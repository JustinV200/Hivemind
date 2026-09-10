"""Tests for hivemind.queen.placement.decide: the Queen's pure Real-vs-Virtual-Cell decision.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.decide for the module under test.
"""

from __future__ import annotations

import pytest
from builders.cells import make_cell

from hivemind.cell import CellKind, Isolation, OsFamily, TaskNeeds
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import Placement, PlacementError, decide
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_hive_id, new_node_id, new_warden_id
from waggle.transport.memory import MemoryTransport


def _make_link(clock: FakeClock, **cell_overrides: object) -> WardenLink:
    """Build one WardenLink over a fresh, unused MemoryTransport pair."""
    warden_id = new_warden_id(clock)
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)
    queen_transport, _warden_transport = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=hive_id, recipient=warden_id, node_id=node_id)
    cell = make_cell(kind=CellKind.REAL, clock=clock, **cell_overrides)
    return WardenLink(warden_id=warden_id, cell=cell, transport=queen_transport, hop=hop)


def test_no_wardens_attached_refuses_placement() -> None:
    with pytest.raises(PlacementError):
        decide(TaskNeeds(), ())


def test_places_on_the_first_attached_warden_the_hive_stand() -> None:
    clock = FakeClock()
    link = _make_link(clock)

    placement = decide(TaskNeeds(), (link,))

    assert placement == Placement(cell_id=link.cell.id, warden_id=link.warden_id)


def test_required_isolation_is_refused_since_no_cell_can_isolate_in_v0() -> None:
    clock = FakeClock()
    link = _make_link(clock)

    with pytest.raises(PlacementError):
        decide(TaskNeeds(isolation=Isolation.REQUIRED), (link,))


def test_os_mismatch_is_refused() -> None:
    clock = FakeClock()
    # builders.cells.make_capabilities's own default builds a LINUX Cell.
    link = _make_link(clock)
    needs = TaskNeeds(os=OsFamily.WINDOWS)

    with pytest.raises(PlacementError):
        decide(needs, (link,))


def test_a_matching_os_need_is_accepted() -> None:
    clock = FakeClock()
    link = _make_link(clock)
    needs = TaskNeeds(os=OsFamily.LINUX)

    placement = decide(needs, (link,))

    assert placement.cell_id == link.cell.id
