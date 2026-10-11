"""Tests for hivemind.cli.llm.eval.measured: scores, the score-to-grade mapping, ledger writes.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/measured.py (codingrules section 3). The manifest is a real
    `builders.cli.fake_manifest`, so slot-to-source attribution runs through the production
    conversions.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.measured for the module under test.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.evals import make_goal_result
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cli.llm.eval.grades import QuestionGrade, ToolCallGrade
from hivemind.cli.llm.eval.measured import (
    SlotScore,
    assign_grades,
    grade_for_score,
    score_slots,
    write_measured_grades,
)
from hivemind.manifest import load_manifest
from hivemind.queen.forage.ledger import InMemoryLedgerStore
from waggle.clock import FakeClock

# ──────────────────────────────────────────────────────────────────────────────
# grade_for_score
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        (0.0, 1),
        (0.199, 1),
        (0.2, 2),
        (0.399, 2),
        (0.4, 3),
        (0.6, 4),
        (0.799, 4),
        (0.8, 5),
        (1.0, 5),
    ],
)
def test_grade_for_score_follows_the_documented_bands(score: float, grade: int) -> None:
    assert grade_for_score(score) == grade


@pytest.mark.parametrize(("score", "grade"), [(-3.0, 1), (7.5, 5), (math.nan, 1), (math.inf, 5)])
def test_grade_for_score_clamps_anything_out_of_range(score: float, grade: int) -> None:
    assert grade_for_score(score) == grade


@given(st.floats(min_value=0, max_value=1), st.floats(min_value=0, max_value=1))
def test_grade_for_score_is_monotone_and_always_one_to_five(a: float, b: float) -> None:
    low, high = sorted((a, b))

    assert 1 <= grade_for_score(low) <= grade_for_score(high) <= 5


# ──────────────────────────────────────────────────────────────────────────────
# score_slots
# ──────────────────────────────────────────────────────────────────────────────


def test_score_slots_scores_queen_and_worker_from_their_own_columns() -> None:
    results = [make_goal_result(), make_goal_result()]

    scores = {score.slot: score for score in score_slots(results)}

    assert scores["QUEEN"].score == 1.0
    assert scores["WORKER"].score == 1.0
    assert scores["QUEEN"].goals == 2


def test_score_slots_marks_a_failed_run_down_on_the_columns_that_judge_each_slot() -> None:
    failed_plan = make_goal_result(
        decomposition=make_goal_result().decomposition.model_copy(update={"passed": False}),
        succeeded=False,
        passed=False,
    )

    scores = {score.slot: score for score in score_slots([failed_plan])}

    assert scores["QUEEN"].score == 0.5  # A failed plan with fine needs.
    assert scores["WORKER"].score == pytest.approx(0.5)  # Not succeeded, but valid output.


def test_score_slots_counts_the_question_and_validity_columns_only_when_they_apply() -> None:
    asked = make_goal_result(
        question=QuestionGrade(applicable=True, asked=True, answered=False, passed=False),
        tool_calls=ToolCallGrade(model_calls=4, malformed=2, validity_rate=0.5),
    )

    worker = {s.slot: s for s in score_slots([asked])}["WORKER"]

    assert worker.score == pytest.approx((1.0 + 0.5 + 0.0) / 3)


def test_score_slots_leaves_out_a_slot_that_made_no_calls() -> None:
    queen_only = make_goal_result(slot_calls={"QUEEN": 2})

    assert [score.slot for score in score_slots([queen_only])] == ["QUEEN"]


def test_score_slots_appends_the_transcriber_score_when_given() -> None:
    transcriber = SlotScore(slot="TRANSCRIBER", score=0.9, goals=2)

    scores = score_slots([make_goal_result()], transcriber)

    assert scores[-1] == transcriber


def test_score_slots_scores_nothing_for_a_run_that_made_no_calls() -> None:
    assert score_slots([make_goal_result(slot_calls={})]) == ()


# ──────────────────────────────────────────────────────────────────────────────
# assign_grades and write_measured_grades
# ──────────────────────────────────────────────────────────────────────────────


def test_assign_grades_gives_a_source_the_lowest_of_the_slots_bound_to_it(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path), {})  # Every slot binds one source.
    scores = [
        SlotScore(slot="QUEEN", score=0.9, goals=2),
        SlotScore(slot="WORKER", score=0.3, goals=2),
    ]

    entries = assign_grades(scores, manifest, FakeClock())

    assert len(entries) == 1
    entry = entries[0]
    assert (entry.source_id, entry.slots, entry.score, entry.grade) == (
        "fake_default",
        ("QUEEN", "WORKER"),
        0.3,
        2,
    )
    assert "QUEEN 0.90 over 2" in entry.evidence


def test_assign_grades_skips_a_slot_whose_binding_names_no_source(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path), {})
    unmapped = manifest.model_copy(
        update={"forage": manifest.forage.model_copy(update={"map": {}})}
    )

    assert (
        assign_grades([SlotScore(slot="WORKER", score=1.0, goals=1)], unmapped, FakeClock()) == ()
    )


async def test_write_measured_grades_puts_each_entry_in_the_ledger_store(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path), {})
    entries = assign_grades([SlotScore(slot="WORKER", score=0.65, goals=1)], manifest, FakeClock())
    store = InMemoryLedgerStore()
    clock = FakeClock()

    written = await write_measured_grades(entries, store, clock.now())

    rows = await store.list_measured_grades()
    assert written == 1
    assert [(row.source_id, row.grade, row.measured_at) for row in rows] == [
        ("fake_default", 4, clock.now())
    ]
