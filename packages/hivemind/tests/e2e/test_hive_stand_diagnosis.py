"""End-to-end: the Hive Stand's Warden runs a real Drone to check a lost device's reachability.

Roadmap step 11.9a's other end of an active reconnection (ADR-0056): when a device is lost, the
Queen asks the Warden that is known to be up, the Hive Stand's own, to look from this end. The
unit tests show the task she creates; this one runs it, which is what shows whether the ceiling,
the pin and the objective work together: a real Queen dispatches the task to a real Warden over a
real (in-process) Waggle link, that Warden spawns the real `Drone` role on the Hive Stand's Cell
under the task's goal ceiling, a model scripted at the provider checks the lookup the way the
objective tells it to (a command that writes its output to a file), writes `findings.md` and
closes, the Warden accepts the file, and the Queen completes the task with what the Drone said.

Fits into the Hive:
    Whole-Hive scenario (codingrules section 3, tests/e2e). Exercises
    `hivemind.queen.swarm_gate.unreachable.hive_stand` end to end, through the Queen's dispatcher,
    placement and Guard, the Warden's spawn and the Drone's tools.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 11.9a for the behaviour this proves.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the decision.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_cell
from builders.swarm.parties import make_swarm_node
from e2e.kernel_helpers import (
    HaikuScript,
    PairOptions,
    build_cluster_pair,
    task_status_is,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import CellKind
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import GateContext
from hivemind.queen.swarm_gate.unreachable import HiveStandDiagnosis
from hivemind.workers.roles import worker_for
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_VERDICT = "The fault looks like that device: the name resolves here and its port does not answer."
_CHECK = ["sh", "-c", "getent hosts device.example > out.txt 2>&1; echo exit=$? >> out.txt"]
_STOP_S = 5.0  # A Queen or Warden that never stops fails the test instead of hanging it.
_DONE_S = 30.0  # The task takes a few real seconds; a loaded host gets room, a hang still fails.


def _drone(request: LLMRequest) -> LLMResponse:
    """The scripted Drone: look the way the objective says to, write the findings, then say so."""
    done = tool_round_count(request)
    if done == 0:
        return tool_response(request, [("check", "run_command", {"argv": _CHECK})])
    if done == 1:
        return tool_response(request, [write_call("findings.md", "The name resolves.")])
    return text_response(_VERDICT)


async def test_a_real_drone_checks_a_lost_device_from_the_hive_stand_and_the_task_completes() -> (
    None
):
    clock = SystemClock()
    hive_stand = make_cell(kind=CellKind.REAL, clock=clock, source="hive_stand")
    options = PairOptions(responder=HaikuScript(_drone).responder, cell=hive_stand)
    pair = await build_cluster_pair(clock, {}, worker_for, pair_options=options)
    await pair.warden.start()
    queen_task = asyncio.ensure_future(pair.queen.run())
    warden_task = asyncio.ensure_future(pair.warden.run())
    diagnosis = HiveStandDiagnosis(lambda: GateContext(pair.queen, pair.deps))
    node = make_swarm_node(clock=clock)

    await diagnosis.begin(node, "missed_heartbeats")  # The hold begins: one task is created.
    [created] = await pair.deps.chamber.list(TaskFilter())
    await wait_until(
        lambda: task_status_is(pair.deps, created.id, TaskStatus.SUCCEEDED), timeout_s=_DONE_S
    )
    await diagnosis.end(node)  # The hold ends; a task that ran is left as it finished.

    finished = await pair.deps.chamber.get(created.id)
    assert finished.status is TaskStatus.SUCCEEDED
    assert finished.outcome is not None and _VERDICT in finished.outcome.summary
    # The ceiling let the Drone do all of it: nothing it tried was refused by the Guard.
    assert await pair.deps.trail.query(TrailQuery(kind="guard.denied")) == ()
    await pair.queen.stop()
    await asyncio.wait_for(queen_task, timeout=_STOP_S)
    await pair.warden.stop()
    await asyncio.wait_for(warden_task, timeout=_STOP_S)
