"""Define EvalReport, render it as a markdown table, and write `<date>.md` and `<date>.json`.

The report is the harness's one output (roadmap step 8.5): a pydantic model holding the bindings
that were evaluated, one `GoalResult` per goal (every grader's column), the per-slot scores, the
optional transcriber grade, and the grades `--update-map` measured. `render_markdown` turns it
into the document an operator reads and commits under `docs/evals/`; `write_report` writes that
beside a JSON copy of the same model, so a later run or a script can compare two dates without
parsing a table. The JSON is the contract; the markdown is for people.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Built and written
    by the evaluation pipeline. Calls into this package's grades, measured and transcriber models
    only.

Key invariants:
    - `write_report` is the one function here that touches the filesystem, and only under the
      directory it is given; it never writes anywhere by default. A second report on the same
      date replaces the first (the date is the file name, per the roadmap).
    - Both files hold the same model: `<date>.json` parses back into an equal `EvalReport`.
    - The markdown has one row per goal and one column per grader, so a missing column is a test
      failure, not a silent omission.

See Also:
    - docs/evals/README.md for how to read each column.
    - hivemind.cli.llm.eval.graders for what each column means.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.llm.eval.grades import GoalResult
from hivemind.cli.llm.eval.measured import BAND_LINES, MeasuredEntry, SlotScore
from hivemind.cli.llm.eval.transcriber import TranscriberGrade

DEFAULT_REPORT_DIR = Path("docs/evals")  # Where the repository keeps committed reports.

# The goal table's columns, in order: one per grader, after the goal name and its verdict.
GOAL_COLUMNS: tuple[str, ...] = (
    "Goal",
    "Result",
    "Decomposition",
    "TaskNeeds",
    "Effects",
    "Ask",
    "Tool-call validity",
    "Rung",
    "Alarm rate",
    "Handoff",
    "Steps",
    "Cost",
    "Wall time",
)

__all__ = [
    "DEFAULT_REPORT_DIR",
    "GOAL_COLUMNS",
    "BindingRow",
    "EvalReport",
    "render_markdown",
    "write_report",
]


class BindingRow(BaseModel):
    """One `[llm.slots]` binding the run evaluated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(description="The `[llm.slots]` key.")
    provider: str = Field(description="The `[llm.providers]` name it binds.")
    model: str = Field(description="The model id it binds.")


class EvalReport(BaseModel):
    """Everything one `hive llm eval` run measured, in a form that round-trips through JSON."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date = Field(description="The run's date; also the report's file name.")
    generated_at: datetime = Field(description="When the run finished (UTC).")
    manifest: str = Field(description="The manifest file the bindings came from.")
    bindings: tuple[BindingRow, ...] = Field(description="Every slot binding under evaluation.")
    goals: tuple[GoalResult, ...] = Field(description="One graded result per goal run.")
    slot_scores: tuple[SlotScore, ...] = Field(description="Each exercised slot's 0 to 1 score.")
    transcriber: TranscriberGrade | None = Field(description="The clip-set grade, if run.")
    measured: tuple[MeasuredEntry, ...] = Field(description="The grades the scores map to.")
    map_updated: bool = Field(description="Whether `--update-map` wrote `measured` to the ledger.")


def render_markdown(report: EvalReport) -> str:
    """Render `report` as the markdown document committed under `docs/evals/`."""
    lines = [
        f"# Evaluation {report.day.isoformat()}",
        "",
        f"Manifest: `{report.manifest}`. Generated {report.generated_at.isoformat()}.",
        "",
        "## Bindings",
        "",
        *_table(
            ("Slot key", "Provider", "Model"),
            [(b.key, b.provider, b.model) for b in report.bindings],
        ),
        "",
        "## Goals",
        "",
        *_table(GOAL_COLUMNS, [_goal_row(result) for result in report.goals]),
        *_notes(report.goals),
        "",
        "## Slot scores and measured grades",
        "",
        *_table(
            ("Slot", "Score", "Goals"),
            [(s.slot, f"{s.score:.2f}", str(s.goals)) for s in report.slot_scores],
        ),
        "",
        *_measured_section(report),
    ]
    if report.transcriber is not None:
        lines += ["", *_transcriber_section(report.transcriber)]
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, directory: Path) -> tuple[Path, Path]:
    """Write `<date>.md` and `<date>.json` under `directory`, creating it if needed.

    Args:
        report: The report to write.
        directory: Where to write; `DEFAULT_REPORT_DIR` is the CLI's default, never this
            function's.

    Returns:
        The markdown path and the JSON path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stem = report.day.isoformat()
    markdown, as_json = directory / f"{stem}.md", directory / f"{stem}.json"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    as_json.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return markdown, as_json


def _goal_row(result: GoalResult) -> tuple[str, ...]:
    """Build one goal's table row, one cell per `GOAL_COLUMNS` entry."""
    rate = result.tool_calls.validity_rate
    unpriced = f" ({result.cost.unpriced_calls} unpriced)" if result.cost.unpriced_calls else ""
    return (
        result.goal,
        "PASS" if result.passed else ("ERROR" if result.error else "FAIL"),
        f"{_mark(result.decomposition.passed)} {result.decomposition.task_count} tasks",
        f"{_mark(result.needs.passed)} {result.needs.score:.2f}",
        _mark(result.effects.passed),
        _verdict(result.question.passed),
        "n/a" if rate is None else f"{rate:.0%}",
        result.rung.reached + (" (stepped down)" if result.rung.stepped_down else ""),
        f"{result.alarms.rate:.2f}",
        _verdict(result.handoff.passed),
        str(result.cost.steps),
        f"${result.cost.cost_usd:.4f}{unpriced}",
        f"{result.cost.wall_time_s:.1f}s",
    )


def _notes(results: tuple[GoalResult, ...]) -> list[str]:
    """List what went wrong for each goal that did not pass, one bullet each."""
    notes: list[str] = []
    for result in results:
        if result.error:
            notes.append(f"- `{result.goal}`: run failed: {result.error}")
        if result.effects.missing:
            notes.append(f"- `{result.goal}`: missing {', '.join(result.effects.missing)}")
        if result.needs.mismatches:
            notes.append(
                f"- `{result.goal}`: TaskNeeds differ at {', '.join(result.needs.mismatches)}"
            )
        if result.timed_out:
            notes.append(f"- `{result.goal}`: timed out")
    return ["", "Notes:", "", *notes] if notes else []


def _measured_section(report: EvalReport) -> list[str]:
    """Render the measured grades, and whether they were written to the ledger."""
    if not report.measured:
        return ["No source could be graded: no scored slot is bound to a Forage map source."]
    status = "written to the ledger" if report.map_updated else "not written (no `--update-map`)"
    return [
        f"Measured grades, {status}:",
        "",
        *_table(
            ("Source", "Slots", "Grade", "Evidence"),
            [(m.source_id, ", ".join(m.slots), str(m.grade), m.evidence) for m in report.measured],
        ),
        "",
        "Mapping: " + "; ".join(BAND_LINES) + ".",
    ]


def _transcriber_section(grade: TranscriberGrade) -> list[str]:
    """Render the TRANSCRIBER clip-set grade."""
    rows = [
        (
            c.clip,
            f"{c.wer:.3f}",
            f"{c.latency_s:.2f}s",
            "n/a" if c.real_time_factor is None else f"{c.real_time_factor:.2f}",
        )
        for c in grade.clips
    ]
    return [
        "## Transcriber",
        "",
        f"`{grade.provider}` / `{grade.model}`: mean WER {grade.mean_wer:.3f}, mean latency "
        f"{grade.mean_latency_s:.2f}s, score {grade.score:.2f}.",
        "",
        *_table(("Clip", "WER", "Latency", "Real-time factor"), rows),
    ]


def _table(header: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> list[str]:
    """Render a markdown table; a pipe inside a cell is escaped so it cannot split the row."""

    def line(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"

    return [line(header), line(("---",) * len(header)), *(line(row) for row in rows)]


def _mark(passed: bool) -> str:
    """Return the table mark for a pass or a fail."""
    return "ok" if passed else "FAIL"


def _verdict(passed: bool | None) -> str:
    """Return the table cell for a grade that may not apply to the goal."""
    return "n/a" if passed is None else _mark(passed)
