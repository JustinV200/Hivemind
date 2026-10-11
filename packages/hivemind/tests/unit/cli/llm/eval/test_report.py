"""Tests for hivemind.cli.llm.eval.report: EvalReport, the markdown renderer and the writer.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/report.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.report for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

from builders.evals import make_eval_report, make_goal_result

from hivemind.cli.llm.eval.grades import EffectsGrade, NeedsGrade
from hivemind.cli.llm.eval.report import (
    DEFAULT_REPORT_DIR,
    GOAL_COLUMNS,
    EvalReport,
    render_markdown,
    write_report,
)
from hivemind.cli.llm.eval.transcriber import ClipScore, TranscriberGrade


def _header_cells(markdown: str, after: str) -> list[str]:
    """Return the cells of the first table row following the `after` heading."""
    lines = markdown.splitlines()
    row = lines[lines.index(after) + 2]
    return [cell.strip() for cell in row.strip("|").split(" | ")]


def test_the_default_report_directory_is_docs_evals() -> None:
    assert Path("docs/evals") == DEFAULT_REPORT_DIR


def test_the_goal_table_has_one_column_per_grader() -> None:
    markdown = render_markdown(make_eval_report())

    assert _header_cells(markdown, "## Goals") == list(GOAL_COLUMNS)
    for grader_column in (
        "Decomposition",
        "TaskNeeds",
        "Tool-call validity",
        "Rung",
        "Alarm rate",
        "Handoff",
        "Steps",
        "Cost",
        "Wall time",
    ):
        assert grader_column in GOAL_COLUMNS


def test_the_goal_table_has_one_row_per_goal_with_its_verdict() -> None:
    report = make_eval_report(
        goals=(make_goal_result(goal="haiku"), make_goal_result(goal="ask", passed=False))
    )

    rows = [line for line in render_markdown(report).splitlines() if line.startswith("| ask")]

    assert len(rows) == 1
    assert " FAIL " in rows[0]


def test_a_run_that_errored_reads_error_and_its_message_is_listed() -> None:
    failed = make_goal_result(passed=False, succeeded=False, error="ConnectError: refused")

    markdown = render_markdown(make_eval_report(goals=(failed,)))

    assert "| ERROR |" in markdown
    assert "run failed: ConnectError: refused" in markdown


def test_missing_effects_and_needs_mismatches_are_listed_as_notes() -> None:
    result = make_goal_result(
        passed=False,
        effects=EffectsGrade(expected=("a.txt",), missing=("a.txt",), passed=False),
        needs=NeedsGrade(compared=5, mismatches=("task1.audio",), score=0.8, passed=False),
    )

    markdown = render_markdown(make_eval_report(goals=(result,)))

    assert "missing a.txt" in markdown
    assert "TaskNeeds differ at task1.audio" in markdown


def test_measured_grades_say_whether_they_were_written() -> None:
    assert "not written" in render_markdown(make_eval_report(map_updated=False))
    assert "written to the ledger" in render_markdown(make_eval_report(map_updated=True))


def test_a_report_with_nothing_graded_says_so() -> None:
    markdown = render_markdown(make_eval_report(measured=()))

    assert "No source could be graded" in markdown


def test_the_transcriber_section_appears_only_with_a_clip_grade() -> None:
    clip = ClipScore(clip="a", wer=0.1, latency_s=0.5, duration_s=2.0, real_time_factor=0.25)
    grade = TranscriberGrade(
        provider="scripted-ears",
        model="scripted-model",
        clips=(clip,),
        mean_wer=0.1,
        mean_latency_s=0.5,
        score=0.9,
    )

    assert "## Transcriber" not in render_markdown(make_eval_report())
    assert "## Transcriber" in render_markdown(make_eval_report(transcriber=grade))


def test_a_pipe_inside_a_cell_cannot_split_the_row() -> None:
    markdown = render_markdown(make_eval_report(goals=(make_goal_result(goal="a|b"),)))

    assert "a\\|b" in markdown


def test_write_report_writes_the_markdown_and_json_named_by_date(tmp_path: Path) -> None:
    report = make_eval_report()

    markdown, as_json = write_report(report, tmp_path / "nested" / "reports")

    assert markdown.name == "2026-10-11.md"
    assert as_json.name == "2026-10-11.json"
    assert markdown.read_text(encoding="utf-8") == render_markdown(report)


def test_the_json_file_parses_back_into_an_equal_report(tmp_path: Path) -> None:
    report = make_eval_report(map_updated=True)

    _markdown, as_json = write_report(report, tmp_path)

    assert EvalReport.model_validate_json(as_json.read_text(encoding="utf-8")) == report
    assert json.loads(as_json.read_text(encoding="utf-8"))["goals"][0]["goal"] == "haiku"


def test_a_second_report_on_the_same_date_replaces_the_first(tmp_path: Path) -> None:
    write_report(make_eval_report(manifest="first.toml"), tmp_path)

    markdown, _ = write_report(make_eval_report(manifest="second.toml"), tmp_path)

    assert "second.toml" in markdown.read_text(encoding="utf-8")
    assert len(list(tmp_path.iterdir())) == 2
