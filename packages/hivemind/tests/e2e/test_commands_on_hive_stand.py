"""End-to-end: a Hive Stand below FULL runs no Worker command (ADR-0060).

The Hive Stand is a plain Real Cell: a command run there is a subprocess under the operator's own
account, whatever its working directory. At `access_level = "SCRATCH"` (the manifest's default) a
Drone's `run_command` is refused by the Guard before any proposal exists, the refusal is a
`guard.denied` row, and the Drone reads why and carries on without it. A task whose plan says it
runs commands is never placed there; with no Virtual backend configured it is cancelled at once,
saying so, and so is one whose acceptance runs a command, since the planner sets the need itself.
A task whose plan forgot the need and whose Drone was refused a command comes back with a typed
cause; the Queen re-places it rather than retrying it there, and with nowhere commands run it
fails with placement's reason. Every scenario runs a whole Hive over real SQLite, as `hive run`
composes it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md.
    - tests.unit.workers.tools.test_session and tests.unit.queen.placement.test_decide_commands
      for the same rule over fakes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from builders.cli import fake_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.llm import LLMRequest, LLMResponse, TextPart, ToolResultPart
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # Bound on each goal; each lands within a second or two.
_FILE = "haiku_1.txt"
_REFUSED = "it needs a Virtual Cell"  # What the Drone reads when its command is refused.


def _drone(request: LLMRequest) -> LLMResponse:
    """Try to copy a key into scratch; write the haiku only once the refusal has been read."""
    rounds = tool_round_count(request)
    if rounds == 0:
        copy = ("copy", "run_command", {"argv": ["cp", "/home/operator/.ssh/id_rsa", "."]})
        return tool_response(request, (copy,))
    if rounds == 1 and _read_refusal(request):
        return tool_response(request, (write_call(_FILE),))
    return text_response("One haiku written; the command was not allowed here.")


def _read_refusal(request: LLMRequest) -> bool:
    """Whether a tool result in this attempt's conversation is the Guard's refusal."""
    for message in request.messages:
        for part in message.parts:
            text = part.content if isinstance(part, ToolResultPart) else None
            if isinstance(part, TextPart):
                text = part.text
            if text is not None and _REFUSED in text:
                return True
    return False


def _hive(tmp_path: Path, plan: dict[str, object]) -> Hive:
    """Compose the Hive as `hive run` does, the Hive Stand at its default SCRATCH access."""
    script = HaikuScript(_drone, plan=plan)
    return build_hive(
        load_manifest(fake_manifest(tmp_path), {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )


def test_a_drones_command_on_a_scratch_hive_stand_is_refused_before_it_is_proposed(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path, single_task_plan(_FILE))

    report, denied = asyncio.run(_run(hive))

    assert report.succeeded, report
    assert ("tool_invocation", "guard.access_level.scratch", "exec:cp") in denied
    assert not list((tmp_path / "scratch").rglob("id_rsa"))


def test_a_task_that_runs_commands_with_no_virtual_backend_is_cancelled_with_the_reason(
    tmp_path: Path,
) -> None:
    # The same one-task plan, but its needs say the Worker runs commands.
    planned = single_task_plan(_FILE)
    tasks = cast(list[dict[str, object]], planned["tasks"])
    plan: dict[str, object] = {"tasks": [{**tasks[0], "needs": {"commands": True}}]}
    hive = _hive(tmp_path, plan)

    cancelled = asyncio.run(_cancelled(hive))

    assert cancelled.outcome is not None
    assert "no Virtual backend is configured" in cancelled.outcome.summary
    assert "processes_contained=false" in cancelled.outcome.summary


async def _run(hive: Hive) -> tuple[GoalReport, set[tuple[object, object, object]]]:
    """Run the one-haiku goal to its end; its report and every refusal's point, rule and need."""
    async with run_hive(hive):
        report = await run_goal(
            hive, "write one haiku", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
        )
        return report, await _denied(hive)


async def _cancelled(hive: Hive) -> Task:
    """Submit the goal and wait for its one task to be cancelled by placement."""
    async with run_hive(hive):
        goal_id = await hive.queen.submit_goal("write one haiku", clearance=HoneyClearance.C1)

        async def ended() -> bool:
            tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
            return bool(tasks) and all(t.status is TaskStatus.CANCELLED for t in tasks)

        await wait_until(ended, timeout_s=_TIMEOUT_S)
        [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        return task


async def _denied(hive: Hive) -> set[tuple[object, object, object]]:
    """Each `guard.denied` row's point, rule and capability."""
    rows = await hive.stores.trail.query(TrailQuery(kind="guard.denied"))
    return {(e.payload["point"], e.payload["rule"], e.payload["capability"]) for e in rows}


def _stuck_drone(request: LLMRequest) -> LLMResponse:
    """Try to run the tests, read the refusal, and give up without writing anything."""
    if tool_round_count(request) == 0:
        run = ("tests", "run_command", {"argv": ["pytest", "-q"]})
        return tool_response(request, (run,))
    return text_response("The tests could not run here; nothing was written.")


def test_a_task_whose_command_was_refused_is_re_placed_not_retried_and_fails_with_the_reason(
    tmp_path: Path,
) -> None:
    # The plan never said the Drone runs commands, so the task lands on the SCRATCH Hive Stand.
    script = HaikuScript(_stuck_drone, plan=single_task_plan(_FILE))
    hive = build_hive(
        load_manifest(fake_manifest(tmp_path), {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )

    failed = asyncio.run(_ended(hive, TaskStatus.FAILED))

    assert failed.outcome is not None
    assert "Its Cell refused the commands it runs" in failed.outcome.summary
    assert "no Virtual backend is configured" in failed.outcome.summary
    assert failed.attempt == 1  # Never retried on the Cell that refused it.


def test_a_task_whose_acceptance_runs_a_command_is_never_placed_on_a_scratch_hive_stand(
    tmp_path: Path,
) -> None:
    # The model left needs empty; the planner sets needs.commands from the command criterion.
    planned = single_task_plan(_FILE)
    tasks = cast(list[dict[str, object]], planned["tasks"])
    check = {"kind": "COMMAND_EXITS_ZERO", "subject": "tests", "argv": ["pytest"], "expected": None}
    plan: dict[str, object] = {"tasks": [{**tasks[0], "acceptance": [check]}]}
    hive = _hive(tmp_path, plan)

    cancelled = asyncio.run(_cancelled(hive))

    assert cancelled.spec.needs.commands is True
    assert cancelled.outcome is not None
    assert "no Virtual backend is configured" in cancelled.outcome.summary


async def _ended(hive: Hive, status: TaskStatus) -> Task:
    """Submit the goal and wait for its one task to end in `status`."""
    async with run_hive(hive):
        goal_id = await hive.queen.submit_goal("write one haiku", clearance=HoneyClearance.C1)

        async def ended() -> bool:
            tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
            return bool(tasks) and all(t.status is status for t in tasks)

        await wait_until(ended, timeout_s=_TIMEOUT_S)
        [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        return task
