"""Tests for hivemind.wardens.local_pool.hosting.sources: the ceilings rule and the wire rows.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/sources.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.sources for the module under test.
"""

from __future__ import annotations

from builders.hosting import vram_source

from hivemind.forage import Ceilings
from hivemind.wardens.local_pool.hosting.sources import (
    added_message,
    hosted,
    plan_loads,
    removed_message,
    vram_of,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.forage import SourceRemovalCause

_GIB = 1024**3
_CELL = new_cell_id(FakeClock())


def _ceilings(vram: int, loadable: tuple[str, ...]) -> Ceilings:
    return Ceilings(
        max_sub_bees=2,
        model_vram_bytes=vram,
        model_disk_bytes=0,
        loadable_sources=loadable,
        exportable_seats=0,
    )


def test_with_no_ceilings_every_source_loads() -> None:
    sources = (vram_source("a", _GIB), vram_source("b", 99 * _GIB))

    plan = plan_loads(sources, None, vram_in_use=0)

    assert plan.load == sources
    assert plan.refused == ()


def test_a_source_outside_loadable_sources_is_refused_with_a_reason() -> None:
    sources = (vram_source("a", _GIB), vram_source("b", _GIB))

    plan = plan_loads(sources, _ceilings(8 * _GIB, ("a",)), vram_in_use=0)

    assert [s.source_id for s in plan.load] == ["a"]
    assert plan.refused[0][0] == "b"
    assert "loadable_sources" in plan.refused[0][1]


def test_vram_is_cumulative_so_the_second_source_that_does_not_fit_is_refused() -> None:
    sources = (vram_source("a", 6 * _GIB), vram_source("b", 6 * _GIB))

    plan = plan_loads(sources, _ceilings(8 * _GIB, ("a", "b")), vram_in_use=0)

    assert [s.source_id for s in plan.load] == ["a"]
    assert "VRAM" in plan.refused[0][1]


def test_vram_already_in_use_counts_against_the_ceiling() -> None:
    plan = plan_loads(
        (vram_source("a", 4 * _GIB),), _ceilings(8 * _GIB, ("a",)), vram_in_use=6 * _GIB
    )

    assert plan.load == ()


def test_a_source_that_declares_no_vram_costs_none() -> None:
    from builders.hosting import make_server_config

    source = make_server_config().sources[0]

    assert vram_of(source) == 0
    assert plan_loads((source,), _ceilings(0, (source.source_id,)), 0).load == (source,)


def test_hosted_fills_the_host_cell_and_leaves_the_rest() -> None:
    source = vram_source("a", _GIB)

    placed = hosted(source, _CELL)

    assert placed.spec.host_cell_id == _CELL
    assert source.spec.host_cell_id is None
    assert placed.spec.model == source.spec.model


def test_added_message_carries_the_hand_set_grade_seats_and_vram() -> None:
    placed = hosted(vram_source("a", _GIB), _CELL)

    message = added_message(_CELL, "box", placed, "because")

    assert (message.server_id, message.seats_total, message.vram_bytes) == ("box", 2, _GIB)
    assert message.grade == placed.spec.grade
    assert message.source.host_cell_id == _CELL
    assert message.capabilities == ("json_mode",)


def test_removed_message_names_the_cause() -> None:
    placed = hosted(vram_source("a", _GIB), _CELL)

    message = removed_message(_CELL, "box", placed, SourceRemovalCause.EVICTED, "why")

    assert message.cause is SourceRemovalCause.EVICTED
    assert message.source.source_id == "a"
