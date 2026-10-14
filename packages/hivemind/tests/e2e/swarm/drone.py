"""Script the work a container device is given: the Drone's turns, the plans and the pinned goal.

A container is a Linux machine that is not this one, so the Drone's command has to be one it has:
`sleep` and `sh` from its image, not the interpreter of the test's own virtualenv that
`e2e.swarm.hive.device_worker_turn` runs. `command_turn` is that Drone: one round that runs a
command on the device and writes the report file there, both through the `PollenSession` its
Warden leased, then one line to say so. `Script` is the Hive's whole model for such a scenario: it
answers the Queen's planning call with the plan the test last chose (`plan_for`), so one Hive can
be given a Propolis goal and then a Night Veil one, and every Drone turn with the script it was
built with. `submit_pinned` puts a goal to the Queen whose ceiling names the device's Cell and no
other, so the Queen's placement can choose nothing else, and `finished` waits for its task to end.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 end-to-end
    scenarios that place a task on a container device; builds on `e2e.kernel_helpers` and
    `e2e.swarm.hive`.

Key invariants:
    - The plan a goal is planned with is the one `Script.plan` holds when the Queen asks for it,
      so a test sets it before it submits the goal and not after.
    - The report file is written in scratch and named in the plan's acceptance criterion, so the
      task can succeed only if the Warden finds the file on the device.

See Also:
    - tests/e2e/kernel_helpers.py for HaikuScript and the tool-call scripting this builds on.
    - tests/e2e/test_swarm_level0.py for the same goal on a device in the test's own process.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from e2e.kernel_helpers import (
    HaikuScript,
    WorkerTurn,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)
from e2e.swarm.hive import HELLO, REPORT_FILE

from hivemind.brood_chamber import Task, TaskFilter, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.swarm import SwarmNode
from waggle.ids import TaskId

GOAL_TEXT = "write a report on the borrowed device"
FINISH_S = 90.0  # Longest a goal on a device takes to end, scratch and a held lease included.

__all__ = [
    "FINISH_S",
    "GOAL_TEXT",
    "Script",
    "command_turn",
    "finished",
    "pinned_ceiling",
    "plan_for",
    "submit_pinned",
]


def command_turn(*attempts: Sequence[str]) -> WorkerTurn:
    """Build the Drone's script: run a command on the device, write the report there, say so.

    Each attempt at the task (a first spawn, or a respawn after the Drone failed) runs the next
    command of `attempts`, and the last one is run by every attempt after it. A scenario that cuts
    the device off mid-command gives a long command first and a short one after it, so that a
    task the Hive tries again after the outage leaves nothing running on the device.

    Args:
        *attempts: The commands to run, in programs the device has; at least one.

    Returns:
        A worker turn for `HaikuScript`/`Script`: one round of two tool calls, then a closing line.
    """
    started = -1  # The attempt the Drone is on: a request with no tool result yet begins one.

    def turn(request: LLMRequest) -> LLMResponse:
        nonlocal started
        if tool_round_count(request) > 0:
            return text_response("Ran a command and wrote the report on the device.")
        started += 1
        argv = attempts[min(started, len(attempts) - 1)]
        command = ("run_it", "run_command", {"argv": list(argv)})
        return tool_response(request, (command, write_call(REPORT_FILE, HELLO)))

    return turn


def plan_for(needs: Mapping[str, object] | None = None) -> dict[str, object]:
    """Build the one-task plan whose acceptance criterion is the report file, with `needs`.

    Args:
        needs: The task's needs as the plan spells them, such as `{"comb_shield": "PROPOLIS"}`;
            none (a plain Meadow task) when omitted.

    Returns:
        The plan the Queen's planning call is answered with.
    """
    plan = single_task_plan(REPORT_FILE)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["needs"] = dict(needs or {})
    return plan


class Script:
    """The Hive's scripted model: the Drone's turns, and the plan the next goal is planned as."""

    def __init__(self, worker_turn: WorkerTurn, plan: dict[str, object] | None = None) -> None:
        """Build the script.

        Args:
            worker_turn: Answers every Drone turn.
            plan: The plan the Queen's planning call is answered with; the plain report plan
                when omitted. A test changes `plan` between goals.
        """
        self._worker_turn = worker_turn
        self.plan = plan if plan is not None else plan_for()

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Answer one model request: the plan, a Drone turn, or the kernel suite's other slots."""
        return HaikuScript(self._worker_turn, plan=self.plan).responder(request)


def pinned_ceiling(cell_id: str) -> tuple[str, ...]:
    """Return a goal ceiling that allows the one Real Cell `cell_id` and no other Cell.

    Args:
        cell_id: The device's Cell.

    Returns:
        The capability strings: the Cell, and what a scripted Drone needs to work on it.
    """
    return (
        f"cell:real:{cell_id}",
        "cell:comb_shield:*",
        "llm:*",
        "tool:*",
        "fs:read:**",
        "fs:write:**",
        "exec:*",
        "question:human",
    )


async def submit_pinned(hive: Hive, node: SwarmNode, goal: str = GOAL_TEXT) -> TaskId:
    """Put `goal` to the Queen with a ceiling that names `node`'s Cell and no other.

    Args:
        hive: The running Hive.
        node: The device the goal may be placed on.
        goal: The goal's words.

    Returns:
        The goal's id, the id of its first task.
    """
    ceiling = pinned_ceiling(node.cell_id)
    return await hive.queen.submit_goal(goal, clearance=HoneyClearance.C1, capabilities=ceiling)


async def finished(hive: Hive, goal_id: TaskId, timeout_s: float = FINISH_S) -> Task:
    """Wait for the goal's one task to end, and return it.

    Args:
        hive: The running Hive.
        goal_id: The goal from `submit_pinned`.
        timeout_s: Real seconds to wait.

    Returns:
        The task, in a terminal state.
    """
    filter_ = TaskFilter(goal_id=goal_id)

    async def ended() -> bool:
        tasks = await hive.stores.chamber.list(filter_)
        return bool(tasks) and all(is_terminal(task.status) for task in tasks)

    await wait_until(ended, timeout_s=timeout_s)
    [task] = await hive.stores.chamber.list(filter_)
    return task
