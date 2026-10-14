"""Script the work a container device is given: the Drone's turns, the plans and the pinned goal.

A container is a Linux machine that is not this one, so the Drone's command has to be one it has:
`sleep` and `sh` from its image, not the interpreter of the test's own virtualenv that
`e2e.swarm.hive.device_worker_turn` runs. `command_turn` is that Drone: one round that runs a
command on the device and writes the report file there, both through the `PollenSession` its
Warden leased, then one line to say so. `DeviceDrone` is the packaging scenario's: it also has the
device describe itself into a file and reads that back, because a command's own output never
reaches the model (the Capping gate answers with its verdict), so the file is how the Drone, and
through it the test, learns where the command ran. `Script` is the Hive's whole model for such a
scenario: it answers the Queen's planning call with the plan the test last chose (`plan_for`), so
one Hive can be given a Propolis goal and then a Night Veil one, every Drone turn with the script
it was built with, and the Drone the Hive Stand's own Warden sends to look into a lost device (a
task of the Hive's, not the scenario's) with a script of its own. `submit_pinned` puts a goal to
the Queen whose ceiling names the device's Cell and no other, so the Queen's placement can choose
nothing else; `request_pinned` does the same as a human's own durable goal request that names a
tier (the only way a goal can initiate Night Veil work, ADR-0039), and `finished` waits for its
task to end.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 end-to-end
    scenarios that place a task on a container device; builds on `e2e.kernel_helpers` and
    `e2e.swarm.hive`.

Key invariants:
    - The plan a goal is planned with is the one `Script.plan` holds when the Queen asks for it,
      so a test sets it before it submits the goal and not after.
    - The report file is written in scratch and named in the plan's acceptance criterion, so the
      task can succeed only if the Warden finds the file on the device.
    - A Drone the Queen sent to the Hive Stand to check a lost device is never answered with the
      scenario's script: that script runs commands on a device and counts its own attempts, and
      the Hive Stand's check is neither.
    - The command `DeviceDrone` runs is `sh` and what the base system ships and the packet's own
      binary, because a clean device has no Python for a script to use.

See Also:
    - tests/e2e/kernel_helpers.py for HaikuScript and the tool-call scripting this builds on.
    - tests/e2e/test_swarm_level0.py for the same goal on a device in the test's own process.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from builders.human import make_goal_request
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
from e2e.scripted_openai import PLANNER_TAG
from e2e.swarm.hive import HELLO, REPORT_FILE

from hivemind.brood_chamber import Task, TaskFilter, is_terminal
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.cli.compose import Hive
from hivemind.forage import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, TextPart, ToolResultPart
from hivemind.queen.intake import GoalRequestState
from hivemind.queen.swarm_gate.unreachable.hive_stand import FINDINGS_FILE
from hivemind.swarm import SwarmNode
from waggle.clock import SystemClock
from waggle.ids import TaskId

GOAL_TEXT = "write a report on the borrowed device"
FINISH_S = 90.0  # Longest a goal on a device takes to end, scratch and a held lease included.
# What the Hive Stand's own check of a lost device opens its objective with
# (`hivemind.queen.swarm_gate.unreachable.hive_stand.diagnosis_draft`): how a request for that
# Drone is told from a scenario's own.
CHECK_MARK = "Check reachability of device"
ORDINARY_TITLE = "Do the ordinary work"  # The titles of `exacting_plan`'s two tasks.
EXACTING_TITLE = "Do the exacting work"
INSPECT_FILE = "inspect.txt"  # What `DeviceDrone`'s command leaves in scratch, and it reads back.
# What `DeviceDrone`'s one command reports: the device's own operating system and name, what the
# packet's loader left in the environment of a program it started, and what the packet itself says
# when a command in the lease runs it (a single-file program started by a single-file program: the
# case a half-cleaned environment breaks).
INSPECT = (
    ". /etc/os-release; "
    'echo "os=$PRETTY_NAME"; echo "host=$(hostname)"; echo "loader=[$LD_LIBRARY_PATH]"; '
    'echo "nested=$(pollen version 2>&1)"'
)
_ACTED_AND_READ = 3  # Tool results seen once the first round's two calls and the read are done.
# What the Queen's awake episode decides when the script has no opinion: it is the human's to say.
AWAKE_DECISION = {"action": "ESCALATE_TO_HUMAN", "reason": "Scripted: the human decides."}

__all__ = [
    "CHECK_MARK",
    "EXACTING_TITLE",
    "FINISH_S",
    "GOAL_TEXT",
    "INSPECT",
    "INSPECT_FILE",
    "ORDINARY_TITLE",
    "DeviceDrone",
    "Script",
    "busy_turn",
    "check_turn",
    "command_turn",
    "exacting_plan",
    "finished",
    "is_check_of_a_lost_device",
    "pinned_ceiling",
    "plan_for",
    "request_pinned",
    "request_text",
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


def busy_turn(*argv: str) -> WorkerTurn:
    """Build the Drone of a task that does not end by itself: one command after another.

    It only stops when it is told to, which is how a long task is kept running on a device until
    the Queen moves its Warden: the bee obeys the order to write a Handoff between two commands.

    Args:
        *argv: The command to run every round, in a program the device has.

    Returns:
        A worker turn for `HaikuScript`/`Script`.
    """

    def turn(request: LLMRequest) -> LLMResponse:
        return tool_response(request, (("go_on", "run_command", {"argv": list(argv)}),))

    return turn


def exacting_plan() -> dict[str, object]:
    """Build a plan of two independent tasks, the second of which asks for the best model there is.

    Both write the report file in their own scratch; the second one's accuracy bar is CRITICAL,
    which no local model of a Nuc can meet, so its sub-bee must think on a shared source.
    """
    plan = single_task_plan(REPORT_FILE, key="ordinary", title=ORDINARY_TITLE)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    second = single_task_plan(REPORT_FILE, key="exacting", title=EXACTING_TITLE)["tasks"]
    assert isinstance(second, list)
    exacting = second[0]
    assert isinstance(exacting, dict)
    exacting["needs"] = {"tempo": {"latency_budget_s": None, "accuracy": "CRITICAL"}}
    tasks.append(exacting)
    return plan


class DeviceDrone:
    """The packaging scenario's Drone: a command and a file on a device, then a read of its report.

    Attributes:
        printed: Everything the tools returned in the attempt that read the report, once it has;
            empty before.
    """

    def __init__(self) -> None:
        """Start with nothing shown; `printed` is set when the model sees the report it read."""
        self.printed = ""

    def turn(self, request: LLMRequest) -> LLMResponse:
        """Answer one WORKER-slot call: act, read the command's report back, then say so."""
        rounds = tool_round_count(request)
        if rounds == 0:
            # The command's output goes to a file in scratch (its working directory) because the
            # model is never shown a command's output, only the Capping gate's verdict on it.
            report = f"( {INSPECT} ) > {INSPECT_FILE}"
            command = ("inspect_device", "run_command", {"argv": ["sh", "-c", report]})
            return tool_response(request, (command, write_call(REPORT_FILE, HELLO)))
        if rounds < _ACTED_AND_READ:
            read = ("read_inspection", "read_file", {"path": INSPECT_FILE})
            return tool_response(request, (read,))
        self.printed = _tool_results(request)
        return text_response("Ran a command, wrote the report and read the inspection back.")


def _tool_results(request: LLMRequest) -> str:
    """Everything the tools returned in this attempt, native results and prompted text alike."""
    texts = []
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                texts.append(part.content)
            elif isinstance(part, TextPart) and "Result for " in part.text:
                texts.append(part.text)
    return "\n".join(texts)


def request_text(request: LLMRequest) -> str:
    """Return every word of a request a person could read: the system prompt and each text part."""
    texts = [request.system or ""]
    for message in request.messages:
        texts.extend(part.text for part in message.parts if isinstance(part, TextPart))
    return "\n".join(texts)


def is_check_of_a_lost_device(request: LLMRequest) -> bool:
    """Whether a Drone's request is the Hive Stand's check of a lost device, not a scenario's."""
    return request.slot is ModelSlot.WORKER and CHECK_MARK in request_text(request)


def check_turn(request: LLMRequest) -> LLMResponse:
    """The Hive Stand's check of a lost device, scripted: write the findings it is asked for."""
    if tool_round_count(request) == 0:
        findings = write_call(
            FINDINGS_FILE, "The name resolves here; the fault looks like that device."
        )
        return tool_response(request, (findings,))
    return text_response("The fault looks like that device, not this end of the link.")


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
        # The Queen sends the Hive Stand's own Warden to look into a device that was lost. That
        # Drone is the Hive's, not the scenario's: it must not spend one of the scenario's attempts
        # or run its commands, which belong on the device.
        if is_check_of_a_lost_device(request):
            return check_turn(request)
        # The Queen's other call is her awake episode: a message that needs judgement. The kernel
        # suite never has one (it answers every Queen call with the plan); a device that is moved
        # up the ladder and cut off does, and what the human is to decide is the human's.
        if request.slot is ModelSlot.QUEEN and PLANNER_TAG not in request_text(request):
            return text_response(json.dumps(AWAKE_DECISION))
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


async def request_pinned(
    hive: Hive, node: SwarmNode, comb_shield: CombShieldLevel, goal: str = GOAL_TEXT
) -> TaskId:
    """Put a human's own durable goal request naming `comb_shield` to the Queen, pinned to `node`.

    Unlike `submit_pinned`, whose goal has no durable request behind it, this one is what the
    Hive Entrance writes for a human's ask: origin HUMAN, the tier named and stored, and the
    ceiling that names `node`'s Cell and no other. So when `comb_shield` is NIGHT_VEIL every
    initiation fact (ADR-0039) holds, and the only floor left to refuse the placement is the one
    about where Night Veil work may run.

    Args:
        hive: The running Hive.
        node: The device the goal may be placed on.
        comb_shield: The tier the human asks for.
        goal: The goal's words.

    Returns:
        The goal's id, the id of its first task, once the Queen has planned the request.
    """
    request = make_goal_request(
        SystemClock(),
        text=goal,
        comb_shield=comb_shield,
        capabilities=pinned_ceiling(node.cell_id),
    )
    await hive.queen.request_goal(request)

    async def planned() -> bool:
        return (await hive.stores.goal_requests.get(request.id)).state is GoalRequestState.PLANNED

    # The Queen plans a durable request on her own tick, so the goal exists a moment after this.
    await wait_until(planned, timeout_s=FINISH_S)
    goal_id = (await hive.stores.goal_requests.get(request.id)).goal_id
    assert goal_id is not None  # PLANNED is exactly when the row carries its goal's id.
    return goal_id


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
