"""The evaluation harness (roadmap step 8.5) run whole, on the fake provider.

`hive llm eval` is only as good as the proof that it grades what it claims to, so this runs the
real pipeline: a Hive built from a `builders.cli.fake_manifest` for each of the four fixed goals,
every one scripted to behave (`builders.evals.ScriptedEval`), then graded, scored, written to a
report directory, and, with `--update-map`, written through the Hive's own ledger. The assertions
are the step's exit conditions: a report on disk with a column for every grader, and measured
grades that the *next* Hive's Forage map applies over the manifest's hand-set ones, changing
`effective_grade`, which is the number routing, allocation and the spill rule read. A negative case
proves the graders have teeth: a Drone that writes nothing fails the goal and earns a low grade.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. The `local_llm` variant of this
    run lives in `test_eval_harness_local.py`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 8.5 for the exit condition.
    - tests/evals/README.md for how to run the harness for real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.evals import ScriptedEval, write_fixture_clips

from hivemind.cli.compose import build_hive
from hivemind.cli.llm.eval import (
    EvalReport,
    EvalRequest,
    render_markdown,
    run_evaluation,
    select_goals,
)
from hivemind.cli.llm.eval.report import GOAL_COLUMNS, write_report
from hivemind.cli.llm.eval.runner import RunnerOptions
from hivemind.cli.stores import build_forage_map, open_ledger
from hivemind.forage import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, text_response
from hivemind.llm.transcription import BoundTranscriber, FakeTranscription
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock

_SOURCE_ID = "fake_default"
_HAND_SET_GRADE = 3  # builders.cli's [forage.map.fake_default] grade.
_ALL_GOALS = ("haiku", "command", "ask", "checkpoint")


def _manifest(tmp_path: Path) -> HiveManifest:
    return load_manifest(fake_manifest(tmp_path), {})


def _request(
    tmp_path: Path, manifest: HiveManifest, script: ScriptedEval, **extra: object
) -> EvalRequest:
    """Build a request that runs `script` (or `extra["responder"]` in its place) on a fake Hive."""
    responder = extra.pop("responder", script.responder)
    options = RunnerOptions(
        environ={},
        clock=SystemClock(),
        work_dir=tmp_path / "work",
        responders={"fake": responder},  # type: ignore[dict-item]
        on_hive=script.on_hive,
        force_checkpoints=False,  # The checkpoint script sends its own.
    )
    fields: dict[str, object] = {
        "manifest": manifest,
        "manifest_name": "hive.toml",
        "goals": select_goals(_ALL_GOALS),
        "runner": options,
    }
    fields.update(extra)
    return EvalRequest(**fields)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def graded_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[EvalReport, Path]:
    """Run the four goals once for the read-only assertions below, writing the report."""
    tmp_path = tmp_path_factory.mktemp("eval")
    report = run_evaluation(_request(tmp_path, _manifest(tmp_path), ScriptedEval()))
    write_report(report, tmp_path / "docs-evals")
    return report, tmp_path / "docs-evals"


def test_every_fixed_goal_passes_on_a_well_behaved_fake(
    graded_run: tuple[EvalReport, Path],
) -> None:
    report, _ = graded_run

    assert [(r.goal, r.passed, r.error) for r in report.goals] == [
        (name, True, None) for name in _ALL_GOALS
    ]


def test_each_goal_exercised_the_behaviour_it_measures(graded_run: tuple[EvalReport, Path]) -> None:
    report, _ = graded_run
    by_goal = {result.goal: result for result in report.goals}

    assert by_goal["ask"].question.passed is True
    assert by_goal["checkpoint"].handoff.passed is True
    assert by_goal["checkpoint"].handoff.checkpoints >= 1
    assert by_goal["command"].effects.passed  # answer.txt held the number the command printed.
    assert all(result.decomposition.passed and result.needs.passed for result in report.goals)


def test_the_report_is_written_as_markdown_and_json_with_every_grader_column(
    graded_run: tuple[EvalReport, Path],
) -> None:
    report, directory = graded_run
    stem = report.day.isoformat()

    markdown = (directory / f"{stem}.md").read_text(encoding="utf-8")
    data = json.loads((directory / f"{stem}.json").read_text(encoding="utf-8"))

    assert markdown == render_markdown(report)
    for column in GOAL_COLUMNS:
        assert column in markdown
    graders = {
        "decomposition",
        "needs",
        "effects",
        "question",
        "tool_calls",
        "rung",
        "alarms",
        "handoff",
        "cost",
    }
    for goal in data["goals"]:
        assert graders <= set(goal)
        assert {"steps", "cost_usd", "wall_time_s"} <= set(goal["cost"])


def test_the_run_scored_the_slots_it_exercised_and_graded_their_source(
    graded_run: tuple[EvalReport, Path],
) -> None:
    report, _ = graded_run

    assert [score.slot for score in report.slot_scores] == ["QUEEN", "WORKER"]
    assert [(entry.source_id, entry.grade) for entry in report.measured] == [(_SOURCE_ID, 5)]
    assert report.map_updated is False  # No --update-map: nothing was written.


def test_update_map_writes_grades_the_next_hive_routes_on(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    before = build_forage_map(manifest, SystemClock()).get(_SOURCE_ID)
    ledger = open_ledger(manifest.resolve_path(manifest.hive.db))

    report = run_evaluation(
        _request(tmp_path, manifest, ScriptedEval(), goals=select_goals(("haiku",)), ledger=ledger)
    )

    assert report.map_updated is True
    assert before.effective_grade == _HAND_SET_GRADE
    # The next Hive reads the ledger when it builds its map: the evidence now outranks the guess.
    after = build_hive(manifest, environ={}, clock=SystemClock()).queen_deps.map.get(_SOURCE_ID)
    assert after.effective_grade == 5
    assert after.spec.grade == _HAND_SET_GRADE  # The manifest's own number is untouched.


def test_a_drone_that_does_nothing_fails_the_goal_and_earns_a_low_grade(tmp_path: Path) -> None:
    script = ScriptedEval()

    def lazy_drone(request: LLMRequest) -> LLMResponse:
        # The Queen still plans; the worker just says it is done, writing nothing.
        if request.slot is ModelSlot.WORKER:
            return text_response("Done.")
        return script.responder(request)

    report = run_evaluation(
        _request(
            tmp_path,
            _manifest(tmp_path),
            script,
            responder=lazy_drone,
            goals=select_goals(("haiku",)),
        )
    )

    result = report.goals[0]
    assert result.passed is False
    assert result.effects.missing  # The files never appeared.
    assert report.measured[0].grade < 5


def test_the_transcriber_is_graded_on_a_clip_set_through_the_fake(tmp_path: Path) -> None:
    clips = write_fixture_clips(
        tmp_path / "clips", {"one": "the bees hum", "two": "honey is sweet"}
    )
    fake = FakeTranscription(name="scripted-ears")
    fake.script("the bees hum", "honey is sweet")
    bound = BoundTranscriber(ModelSlot.TRANSCRIBER, "transcriber", fake, "scripted-model")
    manifest = _manifest(tmp_path)

    report = run_evaluation(
        _request(
            tmp_path,
            manifest,
            ScriptedEval(),
            goals=select_goals(("haiku",)),
            fixtures=clips,
            transcriber=bound,
        )
    )

    assert report.transcriber is not None
    assert report.transcriber.mean_wer == 0.0
    assert "## Transcriber" in render_markdown(report)
