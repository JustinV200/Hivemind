"""Provide `hive run`: build a Hive from a manifest and run one goal through it.

`hive run "goal" --manifest hive.toml [--clearance C1] [--timeout 120] [--json]` is the thinnest
possible typer layer over `hivemind.cli.compose`: it loads the manifest, calls `build_hive` with a
`waggle.clock.SystemClock` (codingrules section 11: `SystemClock` only at the command edge),
enters `run_hive`, and awaits `run_goal`, streaming one line per trail event of interest as it
lands (`_STREAMED_KINDS`, roadmap step 3.21's own list: queen decisions, Warden/Worker lifecycle,
Capping's QA-gate cycle, terminal task and Alarm events) unless `--json` was given, in which case
only the final `GoalReport` prints, as one JSON object. `run_command` is a plain function, not
wrapped in its own `typer.Typer` the way every other command group in this package is
(`hivemind.cli.app`'s own docstring calls this out): a `hive run "goal"` invocation has no
subcommand of its own, and a single-command `typer.Typer` added through `add_typer` still demands
one (`hive run run "goal"`) -- verified against this repository's pinned typer version before
writing this module, rather than assumed. `hivemind.cli.app` registers it directly on the root
application with `app.command("run")(run_command)` instead.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.cli.compose`, `hivemind.cli.stores`, `hivemind.manifest`, `hivemind.pheromone` and
    waggle only.

Key invariants:
    - `run_command` never lets a raw `ManifestError`, `LLMError` or any other exception escape as
      a traceback: `hivemind.cli.stores.load_manifest_or_exit` handles a bad manifest (exit 2);
      any other failure during the run itself is this module's own final `except Exception` at
      the top of the command body (codingrules section 10's third allowed broad catch site).
    - The process exit code is 0 only when `GoalReport.succeeded` is True, 2 when
      `GoalReport.timed_out` is True, and 1 for every other failure (an unsucceeded, not-timed-out
      goal, or an exception this command body itself caught).

See Also:
    - .claude/roadmap.md step 3.21 for this command's own roadmap bullet.
    - .claude/codingrules.md section 10 for the three allowed broad `except Exception` sites.
    - hivemind.cli.compose for build_hive/run_hive/run_goal/GoalReport, this module's own seam.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.cli.stores import DEFAULT_MANIFEST, ManifestOption, load_manifest_or_exit
from hivemind.pheromone import PheromoneEvent
from waggle.clock import SystemClock

__all__ = ["run_command"]

DEFAULT_TIMEOUT_S = 120.0  # Two minutes: generous for a multi-step goal against a hosted model.
DEFAULT_CLEARANCE = "C1"  # codingrules 8.9's own default label for ordinary, non-personal work.

# The trail kinds worth a line as `hive run` streams a goal: queen decisions, Warden/Worker
# lifecycle, Capping's own QA-gate cycle, and terminal task/Alarm events (roadmap step 3.21).
_STREAMED_KINDS = frozenset(
    {
        "queen.planned",
        "queen.assigned",
        "warden.started",
        "warden.watch",
        "warden.active",
        "warden.stopped",
        "worker.spawned",
        "worker.started",
        "worker.done",
        "worker.failed",
        "capping.proposed",
        "capping.verified",
        "capping.rejected",
        "capping.rolled_back",
        "task.succeeded",
        "task.failed",
        "alarm.raised",
        "alarm.escalated",
    }
)


def _validate_clearance(value: str) -> str:
    """Typer callback: validate CLEARANCE names a real HoneyClearance member."""
    try:
        HoneyClearance[value]
    except KeyError as exc:
        raise typer.BadParameter(f"{value!r} is not a HoneyClearance (C0, C1 or C2).") from exc
    return value


ClearanceOption = Annotated[
    str,
    typer.Option(
        "--clearance",
        help="The goal's own HoneyClearance: C0, C1 or C2.",
        callback=_validate_clearance,
    ),
]
TimeoutOption = Annotated[
    float, typer.Option("--timeout", help="Seconds to wait for the goal before giving up.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print the final report as JSON only.")]


def run_command(
    goal: Annotated[str, typer.Argument(help="The goal text, exactly as the human stated it.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    clearance: ClearanceOption = DEFAULT_CLEARANCE,
    timeout: TimeoutOption = DEFAULT_TIMEOUT_S,
    as_json: JsonOption = False,
) -> None:
    """Build a Hive from MANIFEST and run GOAL through it, streaming progress as it lands."""
    loaded = load_manifest_or_exit(manifest)
    # build_hive must run outside any event loop: hivemind.cli.stores.open_trail/open_chamber/
    # open_memory (which a stores=None build_hive calls through open_default_stores) each run
    # their own asyncio.run internally, so calling build_hive from inside the asyncio.run below
    # would raise "asyncio.run() cannot be called from a running event loop".
    hive = build_hive(loaded, environ=os.environ, clock=SystemClock())
    try:
        report = asyncio.run(_run(hive, goal, HoneyClearance[clearance], timeout, as_json))
    except Exception as exc:
        # SAFETY: the command body's own broad catch (codingrules section 10's third allowed
        # site): a run that fails for any reason still ends in one clean stderr line and exit 1,
        # never a traceback dumped on an operator's terminal.
        typer.echo(f"hive run failed: {_describe(exc)}", err=True)
        raise typer.Exit(code=1) from exc
    _print_summary(report, as_json)
    if report.timed_out:
        raise typer.Exit(code=2)
    raise typer.Exit(code=0 if report.succeeded else 1)


def _describe(exc: BaseException) -> str:
    """Flatten `exc` into the one line an operator can act on, unwrapping any ExceptionGroup.

    `run_hive` runs the Queen and the Warden inside an `asyncio.TaskGroup`, so a failure anywhere
    in the kernel arrives here wrapped in an `ExceptionGroup` whose own `str` is no more than
    "unhandled errors in a TaskGroup (1 sub-exception)" -- accurate, and useless to whoever has to
    fix it. The cause is what belongs on stderr: which provider refused, which path was missing.

    Args:
        exc: The exception the command body caught; possibly a group, possibly nested.

    Returns:
        Every leaf cause as "TypeName: message", joined by "; " when a group carries more than one.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(inner) for inner in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


async def _run(
    hive: Hive, goal: str, clearance: HoneyClearance, timeout_s: float, as_json: bool
) -> GoalReport:
    """Start and run one goal through `hive`; stream lines unless `as_json`."""
    async with run_hive(hive):
        return await run_goal(
            hive,
            goal,
            clearance=clearance,
            timeout_s=timeout_s,
            on_event=None if as_json else _print_event,
        )


def _print_event(event: PheromoneEvent) -> None:
    """Print one line for a trail event of interest; every other kind is silently skipped."""
    if event.kind in _STREAMED_KINDS:
        typer.echo(f"{event.at.isoformat()}  {event.kind}  {event.subject_id}")


def _print_summary(report: GoalReport, as_json: bool) -> None:
    """Print the final GoalReport: one JSON object, or a short human-readable summary."""
    if as_json:
        typer.echo(_report_json(report))
        return
    outcome = "timed out" if report.timed_out else ("succeeded" if report.succeeded else "failed")
    typer.echo(
        f"goal {report.goal_id} {outcome} in {report.elapsed_s:.1f}s "
        f"({len(report.tasks)} task(s), ${report.spend_usd:.4f} spent)"
    )


def _report_json(report: GoalReport) -> str:
    """Render `report` as one compact JSON line: ids, counts and enums, never a task's full body."""
    return json.dumps(
        {
            "goal_id": report.goal_id,
            "succeeded": report.succeeded,
            "timed_out": report.timed_out,
            "elapsed_s": report.elapsed_s,
            "spend_usd": report.spend_usd,
            "tasks": [{"id": task.id, "status": task.status.value} for task in report.tasks],
        }
    )
