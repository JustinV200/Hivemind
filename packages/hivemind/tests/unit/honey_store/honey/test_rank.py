"""Tests for hivemind.honey_store.honey.rank: score normalisation, fusion and hit selection.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/honey/rank.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.honey.rank for the module under test.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the "Hybrid ranking" formulas.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.honey.rank import (
    RELATIVE_TEXT_FLOOR,
    fuse,
    select,
    text_score,
    text_scores,
    vector_score,
)
from hivemind.honey_store.models import Honey, HoneyPart, NectarOrigin
from waggle.clock import FakeClock
from waggle.ids import HoneyId, NectarId, new_cell_id, new_honey_id, new_nectar_id
from waggle.messages.honey import NectarKind

_CLOCK = FakeClock()  # Mints ids for the text_scores tests; never advanced.
_UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_WEIGHT = st.floats(min_value=0.0, max_value=10.0, allow_nan=False)
_BM25 = st.floats(min_value=-1_000.0, max_value=1_000.0, allow_nan=False)
_DISTANCE = st.floats(min_value=-0.5, max_value=2.5, allow_nan=False)


def _honey(clock: FakeClock, nectar_id: NectarId | None = None, **overrides: object) -> Honey:
    """Build a valid, live SUMMARY Honey row; a fresh id and Nectar unless overridden."""
    body = "A finding about the widget factory."
    fields: dict[str, object] = {
        "id": new_honey_id(clock),
        "nectar_id": nectar_id if nectar_id is not None else new_nectar_id(clock),
        "part": HoneyPart.SUMMARY,
        "chunk_index": 0,
        "title": "Widget factory",
        "summary": body,
        "body": body,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "clearance": HoneyClearance.C1,
        "kind": NectarKind.FINDING,
        "origin": NectarOrigin.BEE,
        "scope": "hive",
        "origin_tier": CombShieldLevel.MEADOW,
        "task_id": None,
        "cell_id": new_cell_id(clock),
        "observed_at": clock.now(),
        "created_at": clock.now(),
        "tainted": False,
    }
    fields.update(overrides)
    return Honey(**fields)


# ──────────────────────────────────────────────────────────────────────────────
# Normalisation
# ──────────────────────────────────────────────────────────────────────────────


def test_text_score_maps_bm25_through_x_over_one_plus_x() -> None:
    assert text_score(-1.0) == pytest.approx(0.5)
    assert text_score(-3.0) == pytest.approx(0.75)


def test_text_score_is_zero_for_a_non_negative_bm25() -> None:
    assert text_score(0.0) == 0.0
    assert text_score(2.5) == 0.0


def test_text_scores_keep_the_best_match_findable_when_bm25_is_near_zero() -> None:
    # A young store: every word is in half the rows or more, so bm25 is about -1e-6 for all.
    first, second = new_honey_id(_CLOCK), new_honey_id(_CLOCK)

    scores = text_scores({first: -3e-6, second: -1.5e-6})

    assert scores[first] == pytest.approx(RELATIVE_TEXT_FLOOR)
    assert scores[second] == pytest.approx(RELATIVE_TEXT_FLOOR / 2)


def test_text_scores_keep_the_absolute_score_when_it_is_higher() -> None:
    strong, weak = new_honey_id(_CLOCK), new_honey_id(_CLOCK)

    scores = text_scores({strong: -9.0, weak: -0.01})

    assert scores[strong] == pytest.approx(text_score(-9.0))  # 0.9, above the relative floor.
    assert scores[weak] < 0.01  # A near-worthless match next to a strong one stays near zero.


def test_text_scores_of_nothing_is_nothing() -> None:
    assert text_scores({}) == {}


@given(st.lists(st.floats(min_value=-50.0, max_value=5.0), min_size=1, max_size=20))
def test_text_scores_are_in_range_and_never_reward_a_worse_bm25(values: list[float]) -> None:
    ids = [new_honey_id(_CLOCK) for _ in values]

    scores = text_scores(dict(zip(ids, values, strict=True)))

    assert all(0.0 <= score < 1.0 for score in scores.values())
    ranked = sorted(zip(values, ids, strict=True))  # Most negative (best) bm25 first.
    for (better_bm25, better), (worse_bm25, worse) in pairwise(ranked):
        if better_bm25 < worse_bm25:
            assert scores[better] >= scores[worse]


def test_vector_score_is_one_minus_distance_clamped_to_the_unit_interval() -> None:
    assert vector_score(0.0) == 1.0
    assert vector_score(0.25) == pytest.approx(0.75)
    assert vector_score(1.5) == 0.0  # Past orthogonal: no evidence, never negative.
    assert vector_score(-1e-9) == 1.0  # Float noise below zero distance still caps at 1.


@given(better=_BM25, worse=_BM25)
def test_text_score_is_in_range_and_never_rewards_a_worse_bm25(better: float, worse: float) -> None:
    low, high = sorted((better, worse))  # A lower bm25 is the better match.

    assert 0.0 <= text_score(high) <= text_score(low) < 1.0


@given(near=_DISTANCE, far=_DISTANCE)
def test_vector_score_is_in_range_and_never_rewards_a_farther_vector(
    near: float, far: float
) -> None:
    low, high = sorted((near, far))

    assert 0.0 <= vector_score(high) <= vector_score(low) <= 1.0


# ──────────────────────────────────────────────────────────────────────────────
# Fusion
# ──────────────────────────────────────────────────────────────────────────────


def test_fuse_takes_the_weighted_mean_and_scores_a_missing_side_zero() -> None:
    clock = FakeClock()
    both, text_only, vector_only = (new_honey_id(clock) for _ in range(3))

    fused = fuse({both: 0.5, text_only: 1.0}, {both: 1.0, vector_only: 0.5}, 0.4, 0.6)

    assert fused[both] == pytest.approx(0.8)  # (0.4 * 0.5 + 0.6 * 1.0) / 1.0
    assert fused[text_only] == pytest.approx(0.4)
    assert fused[vector_only] == pytest.approx(0.3)


def test_fuse_with_a_zero_vector_weight_is_the_text_score_itself() -> None:
    clock = FakeClock()
    honey_id = new_honey_id(clock)

    fused = fuse({honey_id: 0.7}, {}, 1.0, 0.0)

    assert fused[honey_id] == pytest.approx(0.7)


@pytest.mark.parametrize(("fts_weight", "vector_weight"), [(0.0, 0.0), (-0.1, 1.0), (1.0, -0.1)])
def test_fuse_refuses_weights_with_no_positive_sum_or_a_negative_one(
    fts_weight: float, vector_weight: float
) -> None:
    with pytest.raises(ValueError, match="non-negative weights"):
        fuse({}, {}, fts_weight, vector_weight)


@given(text=_UNIT, vector=_UNIT, bump=_UNIT, fts_weight=_WEIGHT, vector_weight=_WEIGHT)
def test_fuse_is_in_range_and_monotone_in_each_input(
    text: float, vector: float, bump: float, fts_weight: float, vector_weight: float
) -> None:
    if fts_weight + vector_weight <= 0:
        return  # Refused by design (tested above); no score to check.
    honey_id = HoneyId("honey_01ARZ3NDEKTSV4RRFFQ69G5FAV")
    base = fuse({honey_id: text}, {honey_id: vector}, fts_weight, vector_weight)[honey_id]
    more_text = min(1.0, text + bump)
    more_vector = min(1.0, vector + bump)

    raised_text = fuse({honey_id: more_text}, {honey_id: vector}, fts_weight, vector_weight)
    raised_vector = fuse({honey_id: text}, {honey_id: more_vector}, fts_weight, vector_weight)

    assert 0.0 <= base <= 1.0
    assert raised_text[honey_id] >= base - 1e-12
    assert raised_vector[honey_id] >= base - 1e-12


# ──────────────────────────────────────────────────────────────────────────────
# Selection
# ──────────────────────────────────────────────────────────────────────────────


def test_select_orders_by_score_and_drops_everything_under_the_floor() -> None:
    clock = FakeClock()
    best, middle, weak = (_honey(clock) for _ in range(3))
    scored = {best.id: 0.9, middle.id: 0.5, weak.id: 0.04}
    rows = {row.id: row for row in (best, middle, weak)}

    selection = select(scored, rows, min_score=0.05, max_hits=10, max_hits_per_nectar=2)

    assert [ranked.honey.id for ranked in selection.ranked] == [best.id, middle.id]
    assert [ranked.score for ranked in selection.ranked] == [0.9, 0.5]
    assert selection.is_cut is False  # The floor is policy, not truncation.


def test_select_caps_hits_per_nectar_and_lets_a_later_nectar_fill_in() -> None:
    clock = FakeClock()
    nectar = new_nectar_id(clock)
    chunks = [_honey(clock, nectar_id=nectar, part=HoneyPart.CHUNK) for _ in range(3)]
    other = _honey(clock)
    scored = {chunks[0].id: 0.9, chunks[1].id: 0.8, chunks[2].id: 0.7, other.id: 0.6}
    rows = {row.id: row for row in (*chunks, other)}

    selection = select(scored, rows, min_score=0.0, max_hits=10, max_hits_per_nectar=2)

    assert [ranked.honey.id for ranked in selection.ranked] == [
        chunks[0].id,
        chunks[1].id,
        other.id,
    ]


def test_select_stops_at_max_hits_and_says_it_cut_the_result() -> None:
    clock = FakeClock()
    rows = [_honey(clock) for _ in range(4)]
    scored = {row.id: 0.9 - index * 0.1 for index, row in enumerate(rows)}

    selection = select(
        scored, {row.id: row for row in rows}, min_score=0.0, max_hits=2, max_hits_per_nectar=2
    )

    assert [ranked.honey.id for ranked in selection.ranked] == [rows[0].id, rows[1].id]
    assert selection.is_cut is True


def test_select_breaks_a_score_tie_by_newer_created_at_then_by_id() -> None:
    clock = FakeClock()
    older = _honey(clock, created_at=clock.now() - timedelta(days=1))
    newer_a = _honey(clock, id=HoneyId("honey_01ARZ3NDEKTSV4RRFFQ69G5FAA"))
    newer_b = _honey(clock, id=HoneyId("honey_01ARZ3NDEKTSV4RRFFQ69G5FAB"))
    rows = {row.id: row for row in (older, newer_b, newer_a)}

    selection = select(
        dict.fromkeys(rows, 0.5), rows, min_score=0.0, max_hits=10, max_hits_per_nectar=2
    )

    assert [ranked.honey.id for ranked in selection.ranked] == [newer_a.id, newer_b.id, older.id]


def test_select_returns_nothing_for_nothing_scored() -> None:
    selection = select({}, {}, min_score=0.0, max_hits=5, max_hits_per_nectar=2)

    assert selection.ranked == ()
    assert selection.is_cut is False
