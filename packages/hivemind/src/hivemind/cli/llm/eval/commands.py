"""Define `hive llm eval`: run the fixed goal set against the manifest's bindings and report.

The command is a thin typer layer over `hivemind.cli.llm.eval.pipeline.run_evaluation` (codingrules
section 2's CLI row: commands call into subsystem APIs, never contain logic). It loads the
manifest, selects the goals (`--goals`, default all), runs them in a private working directory so
the operator's own Hive state is untouched, prints one line per goal as it finishes, writes
`<date>.md` and `<date>.json` under `--report-dir` (default `docs/evals`), and with `--update-map`
writes the measured grades through the Hive's own ledger so the next Hive routes on evidence.
`--fixtures DIR` also grades the TRANSCRIBER slot on a directory of `<clip>.wav` + `<clip>.txt`
pairs.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Registered on
    the `hive llm` group by `hivemind.cli.llm.commands`. Calls into this package's pipeline,
    report and goals and `hivemind.cli.stores` only.

Key invariants:
    - A bad `--manifest`, an unknown goal name or a bad fixture directory prints one line on
      stderr and exits 2; a completed run exits 0 whatever the goals scored, because the grades
      are the output and not a pass/fail of the command.
    - Nothing is written outside `--report-dir` and the temporary working directory, except the
      ledger rows `--update-map` asks for, in the manifest's own `[hive] db`.

See Also:
    - docs/evals/README.md for what the report contains and how to run this on a local model.
    - hivemind.cli.run for the `hive run` command whose Hive this builds per goal.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.llm.eval.goals import UnknownGoalError, select_goals
from hivemind.cli.llm.eval.grades import GoalResult
from hivemind.cli.llm.eval.pipeline import EvalRequest, run_evaluation
from hivemind.cli.llm.eval.report import DEFAULT_REPORT_DIR, EvalReport, write_report
from hivemind.cli.llm.eval.runner import RunnerOptions
from hivemind.cli.llm.eval.transcriber import FixtureSetError
from hivemind.cli.stores import DEFAULT_MANIFEST, ManifestOption, load_manifest_or_exit, open_ledger
from waggle.clock import SystemClock

__all__ = ["register_eval_command"]


def register_eval_command(app: typer.Typer) -> None:
    """Attach `eval` to the `hive llm` group `app`."""
    app.command("eval")(eval_command)


def eval_command(
    manifest: ManifestOption = DEFAULT_MANIFEST,
    goals: Annotated[
        str | None, typer.Option("--goals", help="Comma-separated goal names; default all.")
    ] = None,
    update_map: Annotated[
        bool,
        typer.Option(
            "--update-map",
            help="Write the measured grades to the Hive's ledger so routing uses them.",
        ),
    ] = False,
    report_dir: Annotated[
        Path, typer.Option("--report-dir", help="Where to write <date>.md and <date>.json.")
    ] = DEFAULT_REPORT_DIR,
    fixtures: Annotated[
        Path | None,
        typer.Option("--fixtures", help="A directory of <clip>.wav + <clip>.txt pairs to grade."),
    ] = None,
) -> None:
    """Run the fixed goal set on MANIFEST's bindings, grade it, and write a report."""
    loaded = load_manifest_or_exit(manifest)
    try:
        selected = select_goals(_names(goals))
    except UnknownGoalError as exc:
        typer.echo(str(exc.args[0]), err=True)
        raise typer.Exit(code=2) from exc
    # Every goal gets its own subdirectory here, removed when the run ends.
    with tempfile.TemporaryDirectory(prefix="hive-eval-") as scratch:
        options = RunnerOptions(os.environ, SystemClock(), Path(scratch))
        request = EvalRequest(
            manifest=loaded,
            manifest_name=str(manifest),
            goals=selected,
            runner=options,
            fixtures=fixtures,
            ledger=open_ledger(loaded.resolve_path(loaded.hive.db)) if update_map else None,
            on_goal=_print_goal,
        )
        try:
            report = run_evaluation(request)
        except FixtureSetError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
    _print_summary(report, *write_report(report, report_dir))


def _names(goals: str | None) -> tuple[str, ...]:
    """Split `--goals` into names; None or blank means all (an empty tuple)."""
    return tuple(name.strip() for name in (goals or "").split(",") if name.strip())


def _print_goal(result: GoalResult) -> None:
    """Print one line as a goal finishes, so a long local run shows progress."""
    verdict = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
    typer.echo(
        f"{result.goal:<12} {verdict:<5} tasks={result.decomposition.task_count} "
        f"steps={result.cost.steps} cost=${result.cost.cost_usd:.4f} "
        f"time={result.cost.wall_time_s:.1f}s"
    )


def _print_summary(report: EvalReport, markdown: Path, as_json: Path) -> None:
    """Print where the report went, and the measured grades that were (or would be) written."""
    passed = sum(1 for result in report.goals if result.passed)
    typer.echo(f"goals passed: {passed}/{len(report.goals)}")
    for entry in report.measured:
        written = "written" if report.map_updated else "not written"
        typer.echo(f"measured {entry.source_id}: grade {entry.grade} ({written})")
    if report.transcriber is not None:
        typer.echo(
            f"transcriber: mean WER {report.transcriber.mean_wer:.3f}, "
            f"mean latency {report.transcriber.mean_latency_s:.2f}s"
        )
    typer.echo(f"report: {markdown}")
    typer.echo(f"report: {as_json}")
