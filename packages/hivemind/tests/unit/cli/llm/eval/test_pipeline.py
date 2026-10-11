"""Tests for hivemind.cli.llm.eval.pipeline: run, grade, score, write and report.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/pipeline.py (codingrules section 3). The runner is replaced
    by a canned-evidence stand-in, so this tests the wiring (what is graded, scored, written and
    reported) without starting a Hive; the whole thing over a real Hive is the harness test under
    tests/evals/routing.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.pipeline for the module under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.evals import make_evidence, write_fixture_clips
from builders.tasks import make_task
from builders.trail import make_llm_call, make_trail

import hivemind.cli.llm.eval.pipeline as pipeline_module
from hivemind.cli.llm.eval.evidence import GoalEvidence
from hivemind.cli.llm.eval.goals import EvalGoal, select_goals
from hivemind.cli.llm.eval.grades import GoalResult
from hivemind.cli.llm.eval.pipeline import EvalRequest, run_evaluation
from hivemind.cli.llm.eval.runner import RunnerOptions
from hivemind.cli.llm.eval.transcriber import FixtureSetError
from hivemind.forage import ModelSlot
from hivemind.llm.transcription import BoundTranscriber, FakeTranscription
from hivemind.manifest import load_manifest
from hivemind.queen.forage.ledger import InMemoryLedgerStore
from waggle.clock import FakeClock


def _evidence_for(goal: EvalGoal) -> GoalEvidence:
    """Canned evidence of a haiku-shaped run: planned, one task, a QUEEN and a WORKER call."""
    events = (*make_trail(["queen.planned"]), make_llm_call(slot="QUEEN"), make_llm_call())
    files = {effect.path: "x" for effect in goal.effects}
    return make_evidence(goal=goal.name, tasks=(make_task(),), events=events, files=files)


@pytest.fixture
def canned_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the runner with the canned evidence; return the goal names it was asked to run."""
    asked: list[str] = []

    def run(_manifest: object, goal: EvalGoal, _options: object) -> GoalEvidence:
        asked.append(goal.name)
        return _evidence_for(goal)

    monkeypatch.setattr(pipeline_module, "run_goal_evidence", run)
    return asked


def _request(tmp_path: Path, **overrides: object) -> EvalRequest:
    manifest = load_manifest(fake_manifest(tmp_path), {})
    fields: dict[str, object] = {
        "manifest": manifest,
        "manifest_name": "hive.toml",
        "goals": select_goals(("haiku", "command")),
        "runner": RunnerOptions({}, FakeClock(), tmp_path / "work"),
    }
    fields.update(overrides)
    return EvalRequest(**fields)  # type: ignore[arg-type]


def test_every_selected_goal_is_run_in_order_and_reported(
    tmp_path: Path, canned_runs: list[str]
) -> None:
    report = run_evaluation(_request(tmp_path))

    assert canned_runs == ["haiku", "command"]
    assert [result.goal for result in report.goals] == ["haiku", "command"]
    assert report.manifest == "hive.toml"
    assert {binding.key for binding in report.bindings} >= {"queen", "worker"}


def test_the_callback_hears_each_goal_as_it_finishes(
    tmp_path: Path, canned_runs: list[str]
) -> None:
    heard: list[GoalResult] = []

    run_evaluation(_request(tmp_path, on_goal=heard.append))

    assert [result.goal for result in heard] == canned_runs


def test_slots_that_made_calls_are_scored_and_graded_without_writing_by_default(
    tmp_path: Path, canned_runs: list[str]
) -> None:
    report = run_evaluation(_request(tmp_path))

    assert [score.slot for score in report.slot_scores] == ["QUEEN", "WORKER"]
    assert [entry.source_id for entry in report.measured] == ["fake_default"]
    assert report.map_updated is False


def test_update_map_writes_the_measured_grades_through_the_ledger_store(
    tmp_path: Path, canned_runs: list[str]
) -> None:
    store = InMemoryLedgerStore()

    report = run_evaluation(_request(tmp_path, ledger=store))

    rows = asyncio.run(store.list_measured_grades())
    assert report.map_updated is True
    assert [(row.source_id, row.grade) for row in rows] == [
        (entry.source_id, entry.grade) for entry in report.measured
    ]


def test_a_run_that_made_no_calls_grades_nothing_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "run_goal_evidence",
        lambda _m, goal, _o: make_evidence(goal=goal.name, succeeded=False, error="refused"),
    )
    store = InMemoryLedgerStore()

    report = run_evaluation(_request(tmp_path, ledger=store))

    assert report.measured == ()
    assert report.map_updated is False
    assert all(result.error == "refused" for result in report.goals)


def test_fixtures_add_a_transcriber_grade_and_score(tmp_path: Path, canned_runs: list[str]) -> None:
    clips = write_fixture_clips(tmp_path / "clips", {"a": "hello bee", "b": "one two"})
    fake = FakeTranscription(name="scripted-ears")
    fake.script("hello bee", "one two")
    bound = BoundTranscriber(ModelSlot.TRANSCRIBER, "transcriber", fake, "scripted-model")

    report = run_evaluation(_request(tmp_path, fixtures=clips, transcriber=bound))

    assert report.transcriber is not None
    assert report.transcriber.mean_wer == 0.0
    assert report.slot_scores[-1].slot == "TRANSCRIBER"
    assert report.slot_scores[-1].score == 1.0


def test_a_bad_fixture_directory_raises_rather_than_grading_nothing(
    tmp_path: Path, canned_runs: list[str]
) -> None:
    with pytest.raises(FixtureSetError):
        run_evaluation(_request(tmp_path, fixtures=tmp_path / "missing"))
