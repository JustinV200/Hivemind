"""Run the haiku goal on an offline Hive built from the derived manifest, in process or by CLI.

Every case of the offline proof starts the same way: serve a scripted scenario on loopback,
derive the manifest for its URL (`manifest.offline_manifest`), build a Hive from it and run a
goal. This module is that shared motion, in the two forms the proof needs: `run_goal_in_process`
(`build_hive` then `run_goal` inside `run_hive`, as the kernel suite does) and `run_goal_by_cli`
(`hive run` through `typer.testing.CliRunner`, reading the trail from the database afterwards).
Both return the same `Outcome`, so a case asserts on the trail without caring which door it used.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the cases of
    `tests/e2e/offline/`. Calls into `hivemind.cli.compose` (`build_hive`, `run_goal`,
    `run_hive`), `hivemind.cli.app` and `e2e.scripted_openai`.

Key invariants:
    - `build_hive` always runs outside an event loop (it opens SQLite through its own
      `asyncio.run`), so both runners are plain synchronous functions.
    - No provider is passed to `build_hive`: every model call goes through the registry's real
      `openai_compat` adapter over a real loopback socket.

See Also:
    - tests/e2e/test_kernel_on_hive_stand.py for the in-process and CLI shapes this follows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from e2e.offline.reads import trail_of
from typer.testing import CliRunner

from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.compose import GoalReport, build_hive, run_goal, run_hive
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

GOAL = "write three haiku about bees to separate files"
HAIKU_FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")  # What the scripted Drone writes.
TIMEOUT_S = 30.0  # Generous: a goal on a loopback server finishes in a second or two.

__all__ = ["GOAL", "HAIKU_FILES", "TIMEOUT_S", "Outcome", "run_goal_by_cli", "run_goal_in_process"]


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a goal run left: whether it succeeded, and the whole trail it wrote."""

    succeeded: bool
    events: tuple[PheromoneEvent, ...]
    output: str = ""  # What `hive run` printed; empty for an in-process run.
    timed_out: bool = False  # Whether the goal outlasted its timeout (in-process runs only).


def run_goal_in_process(
    manifest: HiveManifest, goal: str = GOAL, timeout_s: float = TIMEOUT_S
) -> Outcome:
    """Build the Hive, run `goal` inside `run_hive`, and return the report's verdict and trail.

    `timeout_s` is the most wall time the goal gets, planning included; a real local model needs
    far more than the scripted server's default.
    """
    hive = build_hive(manifest, environ={}, clock=SystemClock())

    async def run() -> Outcome:
        async with run_hive(hive):
            report: GoalReport = await run_goal(
                hive, goal, clearance=HoneyClearance.C1, timeout_s=timeout_s
            )
        events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
        return Outcome(report.succeeded, tuple(events), timed_out=report.timed_out)

    return asyncio.run(run())


def run_goal_by_cli(manifest: HiveManifest, manifest_path: Path, goal: str = GOAL) -> Outcome:
    """Run `hive run GOAL --manifest PATH` through the CLI runner; read the trail it wrote."""
    result = CliRunner().invoke(
        app, ["run", goal, "--manifest", str(manifest_path), "--timeout", str(TIMEOUT_S)]
    )
    assert result.exit_code == 0, result.output
    succeeded = "succeeded" in result.output.splitlines()[-1]
    return Outcome(succeeded, trail_of(manifest), result.output)
