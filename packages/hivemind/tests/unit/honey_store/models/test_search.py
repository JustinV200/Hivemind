"""Tests for hivemind.honey_store.models.search: ReadFilter/TextCandidate/VectorCandidate/Stats.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/models/search.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.models.search for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.honey_store.models.honey import Honey, HoneyPart
from hivemind.honey_store.models.nectar import NectarOrigin, NectarState
from hivemind.honey_store.models.search import (
    HoneyStats,
    ReadFilter,
    TextCandidate,
    VectorCandidate,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_honey_id, new_nectar_id
from waggle.messages.honey import NectarKind


def _make_honey() -> Honey:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    return Honey(
        id=new_honey_id(clock),
        nectar_id=new_nectar_id(clock),
        part=HoneyPart.SUMMARY,
        chunk_index=0,
        title="A finding",
        summary="A short summary.",
        body="A short summary.",
        body_sha256="a" * 64,
        clearance=HoneyClearance.C1,
        kind=NectarKind.FINDING,
        origin=NectarOrigin.BEE,
        scope="hive",
        origin_tier="MEADOW",
        task_id=None,
        cell_id=cell_id,
        bee=None,
        observed_at=clock.now(),
        created_at=clock.now(),
        tainted=False,
    )


def test_read_filter_defaults_requested_to_empty() -> None:
    filter_ = ReadFilter(readable=("hive",), max_clearance=HoneyClearance.C1)

    assert filter_.requested == ()


def test_read_filter_is_frozen() -> None:
    filter_ = ReadFilter(readable=("hive",), max_clearance=HoneyClearance.C1)

    with pytest.raises(AttributeError):
        filter_.readable = ("cell:x",)  # type: ignore[misc]  # The assignment is the test.


def test_text_candidate_and_vector_candidate_hold_their_fields() -> None:
    honey = _make_honey()

    text = TextCandidate(honey=honey, bm25=-1.5)
    vector = VectorCandidate(honey=honey, distance=0.1)

    assert text.honey is honey
    assert text.bm25 == -1.5
    assert vector.honey is honey
    assert vector.distance == 0.1


def test_honey_stats_round_trips_through_json() -> None:
    stats = HoneyStats(
        nectar_by_state={NectarState.RECEIVED: 1, NectarState.RIPENED: 2},
        honey_by_part={HoneyPart.SUMMARY: 1, HoneyPart.CHUNK: 3},
        honey_tainted=0,
        honey_retired=1,
        honey_by_clearance={HoneyClearance.C1: 4},
        honey_by_scope_kind={"hive": 4},
        vectors_by_model={"test-embed": 4},
        vector_backend="python",
    )

    restored = stats.model_validate_json(stats.model_dump_json())

    assert restored == stats


def test_honey_stats_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        HoneyStats(
            nectar_by_state={},
            honey_by_part={},
            honey_tainted=0,
            honey_retired=0,
            honey_by_clearance={},
            honey_by_scope_kind={},
            vectors_by_model={},
            vector_backend="python",
            nonsense="nope",
        )
