"""Provide `hive run`: build a Hive from a manifest and run one goal through it.

`hive run "goal" --manifest hive.toml [--clearance C1] [--comb-shield night_veil] [--timeout 120]
[--json]` is the thinnest
possible typer layer over `hivemind.cli.compose`: it loads the manifest, calls `build_hive` with a
`waggle.clock.SystemClock` (codingrules section 11: `SystemClock` only at the command edge),
enters `run_hive`, and awaits `run_goal`, streaming one line per trail event of interest as it
lands (`_STREAMED_KINDS`, roadmap step 3.21's own list: queen decisions, Warden/Worker lifecycle,
Capping's QA-gate cycle, terminal task and Alarm events, and every Forage denial with its cause, a
task left waiting for room said so on its own line) unless `--json` was given, in which case
only the final `GoalReport` prints, as one JSON object. `run_command` is a plain function, not
wrapped in its own `typer.Typer` the way every other command group in this package is
(`hivemind.cli.app`'s own docstring calls this out): a `hive run "goal"` invocation has no
subcommand of its own, and a single-command `typer.Typer` added through `add_typer` still demands
one (`hive run run "goal"`) -- verified against this repository's pinned typer version before
writing this module, rather than assumed. `hivemind.cli.app` registers it directly on the root
application with `app.command("run", cls=RunCommand)(run_command)` instead: `RunCommand` carries
the two options past codingrules 5.1's five-parameter cap (`--json` and `--comb-shield`). Roadmap
step 10.3c: `--comb-shield` names the goal's tier explicitly, and the goal is then asked for as a
durable goal request, the one way Night Veil work is initiated (`hivemind.cli.compose.request`);
a request the Queen refuses (a Night Veil goal that asks where its Cell is, say) prints as a
refusal, not a failure. Without it, the goal is submitted directly, exactly as before.

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
      `GoalReport.timed_out` is True (or a requested goal was still unplanned at the timeout),
      and 1 for every other failure (an unsucceeded, not-timed-out goal, a refused request, or an
      exception this command body itself caught).

See Also:
    - .claude/roadmap.md step 3.21 for this command's own roadmap bullet.
    - .claude/codingrules.md section 10 for the three allowed broad `except Exception` sites.
    - hivemind.cli.compose for build_hive/run_hive/run_goal/GoalReport, this module's own seam.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn, cast

import typer
from typer.core import TyperCommand, TyperOption

from hivemind.brood_chamber import Task
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.cli.compose.hive import run_requested_goal
from hivemind.cli.compose.request import GoalAsk, GoalNotPlannedError
from hivemind.cli.stores import DEFAULT_MANIFEST, ManifestOption, load_manifest_or_exit
from hivemind.pheromone import PheromoneEvent
from waggle.clock import SystemClock

__all__ = ["RunCommand", "run_command"]

DEFAULT_TIMEOUT_S = 120.0  # Two minutes: generous for a multi-step goal against a hosted model.
DEFAULT_CLEARANCE = "C1"  # codingrules 8.9's own default label for ordinary, non-personal work.

# The trail kinds worth a line as `hive run` streams a goal: queen decisions, Warden/Worker
# lifecycle, Capping's own QA-gate cycle, and terminal task/Alarm events (roadmap step 3.21), plus
# every Forage denial (the zero-grant fix), so a task waiting for room never looks like a stall.
_STREAMED_KINDS = frozenset(
    {
        "queen.planned",
        "queen.assigned",
        "forage.denied",
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

# Where `RunCommand` leaves its two options' values on the command's context (`ctx.meta`).
_JSON_META = "hivemind.cli.run.json"
_COMB_SHIELD_META = "hivemind.cli.run.comb_shield"


def _keep_json(ctx: typer.Context, _param: object, value: bool) -> None:
    """Option callback: leave `--json` on the context for `run_command`."""
    ctx.meta[_JSON_META] = bool(value)


def _keep_comb_shield(ctx: typer.Context, _param: object, value: str | None) -> None:
    """Option callback: validate `--comb-shield` names a tier, in any case, and leave it."""
    if value is None:
        return  # Not given: the goal is submitted directly, exactly as before.
    try:
        ctx.meta[_COMB_SHIELD_META] = CombShieldLevel[value.upper()]
    except KeyError as exc:
        raise typer.BadParameter(
            f"{value!r} is not a Comb Shield tier (meadow, propolis or night_veil)."
        ) from exc


class RunCommand(TyperCommand):
    """`hive run`'s command: the options `run_command` declares, plus `--json` and `--comb-shield`.

    Codingrules 5.1 caps a function at five parameters, and typer turns each parameter into one
    option, so the two options past that cap are declared here, on the command itself; each one's
    callback leaves its value on the context, where `run_command` reads it.
    """

    def __init__(self, name: str | None, **settings: object) -> None:
        """Build the command as typer does, then add the two context-carried options."""
        # typer passes every other setting by keyword, exactly as TyperCommand itself takes them.
        super().__init__(name, **cast("dict[str, Any]", settings))
        self.params.append(
            TyperOption(
                param_decls=["--json"],
                is_flag=True,
                default=False,
                expose_value=False,
                callback=_keep_json,
                help="Print the final report as JSON only.",
            )
        )
        self.params.append(
            TyperOption(
                param_decls=["--comb-shield"],
                default=None,
                expose_value=False,
                callback=_keep_comb_shield,
                help="Ask for the goal at this tier (meadow, propolis or night_veil), as a "
                "goal request (roadmap step 10.3c).",
            )
        )


def run_command(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument(help="The goal text, exactly as the human stated it.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    clearance: ClearanceOption = DEFAULT_CLEARANCE,
    timeout: TimeoutOption = DEFAULT_TIMEOUT_S,
) -> None:
    """Build a Hive from MANIFEST and run GOAL through it, streaming progress as it lands."""
    # `--json` and `--comb-shield` arrive on the context (`RunCommand`'s own docstring).
    as_json = bool(ctx.meta.get(_JSON_META, False))
    tier: CombShieldLevel | None = ctx.meta.get(_COMB_SHIELD_META)
    loaded = load_manifest_or_exit(manifest)
    # build_hive must run outside any event loop: hivemind.cli.stores.open_trail/open_chamber/
    # open_memory (which a stores=None build_hive calls through open_default_stores) each run
    # their own asyncio.run internally, so calling build_hive from inside the asyncio.run below
    # would raise "asyncio.run() cannot be called from a running event loop".
    hive = build_hive(loaded, environ=os.environ, clock=SystemClock())
    options = _RunOptions(HoneyClearance[clearance], timeout, as_json, tier)
    try:
        result = asyncio.run(_run(hive, goal, options))
    except Exception as exc:
        # SAFETY: the command body's own broad catch (codingrules section 10's third allowed
        # site): a run that fails for any reason still ends in one clean stderr line and exit 1,
        # never a traceback dumped on an operator's terminal.
        typer.echo(f"hive run failed: {_describe(exc)}", err=True)
        raise typer.Exit(code=1) from exc
    if isinstance(result, GoalNotPlannedError):
        _exit_unplanned(result)
    report = result
    _print_summary(report, as_json)
    if loaded.hive_stand.keep_scratch and not as_json:
        _print_kept_scratch(loaded.resolve_path(loaded.hive_stand.scratch_root))
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


@dataclass(frozen=True, slots=True)
class _RunOptions:
    """How one goal is run (codingrules 5.1's argument group for `_run`).

    Attributes:
        clearance: The goal's own HoneyClearance.
        timeout_s: Seconds to wait for the goal, planning included.
        as_json: Print only the final report, as JSON.
        comb_shield: The tier the operator named, or None to submit the goal directly.
    """

    clearance: HoneyClearance
    timeout_s: float
    as_json: bool
    comb_shield: CombShieldLevel | None


async def _run(hive: Hive, goal: str, options: _RunOptions) -> GoalReport | GoalNotPlannedError:
    """Start and run one goal through `hive`; stream lines unless `as_json`.

    A requested goal that is never planned is returned, not raised, so it leaves `run_hive`
    as a plain outcome rather than a failure of the Hive's own tasks.
    """
    on_event = None if options.as_json else _print_event
    async with run_hive(hive):
        if options.comb_shield is None:
            return await run_goal(
                hive,
                goal,
                clearance=options.clearance,
                timeout_s=options.timeout_s,
                on_event=on_event,
            )
        ask = GoalAsk(goal, options.clearance, options.comb_shield)
        try:
            return await run_requested_goal(
                hive, ask, timeout_s=options.timeout_s, on_event=on_event
            )
        except GoalNotPlannedError as unplanned:
            return unplanned


def _exit_unplanned(unplanned: GoalNotPlannedError) -> NoReturn:
    """Print why a requested goal was never planned, then exit: 1 refused, 2 timed out."""
    if unplanned.refusal is None:
        typer.echo(f"hive run timed out: {unplanned}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"hive run refused: {unplanned.refusal}", err=True)
    raise typer.Exit(code=1)


def _print_event(event: PheromoneEvent) -> None:
    """Print one line for a trail event of interest; every other kind is silently skipped."""
    if event.kind in _STREAMED_KINDS:
        typer.echo(f"{event.at.isoformat()}  {event.kind}  {event.subject_id}{_cause(event)}")


def _cause(event: PheromoneEvent) -> str:
    """Return a Forage denial's own cause, as the rest of its line; empty for every other kind.

    A waiting task (`deferred = true`: the Queen tries it again on every pass) and a failed one
    both carry the limit to blame (`limited_by`), so the operator reads on screen whether the
    goal is waiting for the host, waiting for its own tasks, or has been refused for good.
    """
    if event.kind != "forage.denied":
        return ""
    limit = event.payload.get("limited_by") or "no model binding"
    if event.payload.get("deferred") is not True:
        return f"  denied: {limit}"
    patience_s = event.payload.get("patience_s")
    # A wait on the host's live figures is bounded by [forage] zero_grant_patience_s; a wait on
    # the goal's own allowance ends when one of its own running tasks does.
    until = (
        f"fails after {patience_s:.0f}s"
        if isinstance(patience_s, int | float)
        else "until one of its goal's running tasks finishes"
    )
    return f"  deferred: waiting for {limit} ({until})"


def _print_summary(report: GoalReport, as_json: bool) -> None:
    """Print the final GoalReport: one JSON object, or a short human-readable summary."""
    if as_json:
        typer.echo(_report_json(report))
        return
    # A finished task's outcome summary is the bee's own closing text as the Warden verified it
    # (bounded by MAX_SUMMARY_CHARS); a task still in flight has at most a progress summary. For a
    # goal whose result is a message, this is the message. Printed before the verdict so the
    # one-line verdict stays the last thing on the terminal.
    for task in report.tasks:
        typer.echo(f"  [{task.status.value}] {task.spec.title}")
        summary = _task_summary(task)
        if summary:
            typer.echo(f"    {summary}")
    outcome = "timed out" if report.timed_out else ("succeeded" if report.succeeded else "failed")
    typer.echo(
        f"goal {report.goal_id} {outcome} in {report.elapsed_s:.1f}s "
        f"({len(report.tasks)} task(s), ${report.spend_usd:.4f} spent)"
    )


def _print_kept_scratch(scratch_root: Path) -> None:
    """Name the newest lease directory `[hive_stand] keep_scratch` left behind, if any."""
    leases = sorted(scratch_root.glob("lease_*"), key=lambda path: path.stat().st_mtime)
    if leases:
        typer.echo(f"scratch kept (keep_scratch = true): {leases[-1]}")


def _task_summary(task: Task) -> str | None:
    """Return the text a human should read for `task`: its verified outcome, else its progress."""
    if task.outcome is not None:
        return task.outcome.summary
    return task.last_summary


def _report_json(report: GoalReport) -> str:
    """Render `report` as one compact JSON line: ids, statuses, bounded summaries; no full body."""
    return json.dumps(
        {
            "goal_id": report.goal_id,
            "succeeded": report.succeeded,
            "timed_out": report.timed_out,
            "elapsed_s": report.elapsed_s,
            "spend_usd": report.spend_usd,
            "tasks": [
                {"id": task.id, "status": task.status.value, "summary": _task_summary(task)}
                for task in report.tasks
            ],
        }
    )
