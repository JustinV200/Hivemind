"""End to end: a goal whose first task fails for good ends at once, its dependant cancelled.

Handoff known issue 3, reproduced for real before its fix: a Drone that never writes its file
fails acceptance on every attempt, the Queen fails the task, and its dependant used to stay
PENDING until `hive run` gave up. This run goes through every real layer (the openai_compat
adapter over a real loopback socket to the scripted model server, the Queen, the Hive Stand's
Warden and a real Drone on a real lease) and asserts the goal ends long before its timeout.

Fits into the Hive:
    Whole-Hive scenario (codingrules 14.2, `e2e`). Mirrors no single module: it exercises
    hivemind.brood_chamber.task.graph.stranded_tasks through the Queen's tick.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/scripted_openai.py for the scripted model server.
    - tests/unit/queen/test_queen_results.py for the same rule at the unit level.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from e2e.scripted_openai import Scenario, serve_in_thread, write_manifest

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock
from waggle.ids import new_hive_id, new_node_id

_TIMEOUT_S = 60.0  # Far above the run's real length; the assertion is that it is not reached.
_CHILD = {
    "key": "child",
    "title": "Needs the root",
    "objective": "Runs only after the root task succeeds.",
    "acceptance": [{"kind": "FILE_EXISTS", "subject": "child.txt", "argv": [], "expected": None}],
    "needs": {},
    "clearance": "C1",
    "depends_on": ["root"],
}


@pytest.mark.e2e
def test_a_goal_whose_root_fails_ends_with_its_dependant_cancelled(tmp_path: Path) -> None:
    scenario = Scenario(files=("done.txt",), writes=(), extra_tasks=(_CHILD,))
    clock = SystemClock()

    with serve_in_thread(scenario) as base_url:
        manifest = write_manifest(tmp_path, base_url, (new_hive_id(clock), new_node_id(clock)))
        hive = build_hive(load_manifest(manifest, {}), environ={}, clock=clock)
        report = asyncio.run(_run(hive))

    assert not report.timed_out
    statuses = {task.spec.title: task.status for task in report.tasks}
    assert statuses == {"Do the work": TaskStatus.FAILED, "Needs the root": TaskStatus.CANCELLED}


async def _run(hive: Hive) -> GoalReport:
    """Run the goal inside a running Hive and return its report."""
    async with run_hive(hive):
        return await run_goal(
            hive, "Two dependent tasks", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
        )
