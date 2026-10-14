"""Define HiveStandDiagnosis: the Hive Stand's own Drone checks why a lost device cannot be reached.

While the Queen holds a lost device's tasks she wants to know whether the fault is the device or
this end of the link (ADR-0056, roadmap 11.9a). A device's own Warden cannot say: at Level 0 it
lives here but its hands are on the lost device, and at Level 1 and above it went with the device.
So the Queen asks the Warden that is known to be up, `W0`, the Warden of the Hive Stand's own Cell.
`begin` creates one task for it, directly and without the planner: a Drone at low tempo, asked by
the Queen (`RequestOrigin.QUEEN`), pinned to the Hive Stand by its goal ceiling, whose objective is
to check reachability of the device from here, its name and route to its last address and whether
other Swarm nodes are affected too, which rules out "my network" against "that device". The Drone
only looks: its ceiling gives it commands and files and no way to ask anybody anything. `W0`
runs it like any task, accepts it on the file it writes, and the Queen deposits its outcome as
Nectar as she does for every task she completes, so the finding is waiting before the hold's
"then decide" fires on its own clock. `end` cancels the task if nobody ever started it, because the
device is back or the Queen has decided and nobody is waiting for the answer; one that is running
is left to finish. Nothing here can fail the hold: a task that cannot be created is logged.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.unreachable`. Installed by `hivemind.cli.compose.swarm` through
    `SwarmGate.use_diagnosis`; called by `.devices` at the start and end of each hold. Calls into
    `hivemind.brood_chamber` (the task draft and the chamber), `hivemind.cell`, `hivemind.forage`
    (tempo), `hivemind.queen.swarm_gate.terms`, `hivemind.queen.trail`, `hivemind.swarm` and waggle
    only.

Key invariants:
    - One task per hold that holds tasks, created when the hold begins and never again for the
      same device until its hold ends (a hold with nothing in it asks for none); the Queen's
      autopilot and planner are not involved.
    - The task is pinned to the Hive Stand (`cell:hive_stand`) and can reach no other Cell; its
      ceiling lets it run commands and read and write files, and it holds no question, no network
      of its own and no write outside scratch.
    - A task nobody started is cancelled when the hold ends; one that started is never stopped.
    - A diagnosis learns a node and a reason word, never an address, a key or a path, so the
      objective names the device's Cell and the word the loss was reported with, and no more.
    - No failure to create or cancel the task reaches the hold.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for who investigates from this end.
    - hivemind.queen.swarm_gate.unreachable.diagnosis for the seam this implements.
    - hivemind.queen.ticks.results for the Nectar the Queen deposits when the task completes.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable

from hivemind.brood_chamber import TaskDraft, TaskGraphDraft, TaskStatus
from hivemind.cell import HoneyClearance, RequestOrigin, TaskNeeds
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.queen.swarm_gate.terms import GateContext
from hivemind.queen.trail import record_event
from hivemind.swarm import SwarmNode
from waggle.ids import DeviceId, TaskId
from waggle.messages import Postcondition, PostconditionKind

# A task written to, or cancelled in, the Brood Chamber's own SQLite file: milliseconds, so this
# bound is a hang, not a timing.
DIAGNOSIS_TIMEOUT_S = 10.0
FINDINGS_FILE = "findings.md"  # The file the Drone writes, in its lease's scratch: the acceptance.
# The goal ceiling the task is planned under: the Hive Stand's own Cell, whatever tier it holds,
# commands, files, the Drone's own model slot and its tools. A Drone's role lists its tools as
# `tool:*` and a ceiling keeps only what one of its own entries covers, so naming the tools one
# by one would leave the Drone none of them. What the ceiling takes away is every family the other
# tools then ask for: no `question:human`, no `net`, no write outside scratch, no Cell Wax and no
# spend. (`tool:request` is a family of its own that `tool:*` does not cover.)
CEILING = (
    "cell:hive_stand",
    "cell:comb_shield:*",
    "exec:*",
    "fs:read:**",
    "fs:write:**",
    "llm:worker",
    "tool:*",
)
# What the Drone's command tool does not say, said once: it reports only whether a command worked,
# and every one that exits non-zero counts toward the three that stop a Drone.
COMMAND_HINT = (
    "A command shows you only whether it worked, never what it printed, and three failed "
    "commands stop you. So run each check as an argument list that writes its output to a file "
    "in your scratch directory and always succeeds, for example "
    '`["sh", "-c", "getent hosts <name> > out.txt 2>&1; echo exit=$? >> out.txt"]`, and read '
    "the file back."
)
CANCEL_REASON = "The device is reachable again or the Queen has decided; nobody awaits this check."
# What creating or cancelling the task can fail with; logged, never raised into the hold.
_FAILURES = (HiveMindError, ValueError, TimeoutError, sqlite3.Error)

log = get_logger(__name__)

__all__ = [
    "CANCEL_REASON",
    "CEILING",
    "COMMAND_HINT",
    "DIAGNOSIS_TIMEOUT_S",
    "FINDINGS_FILE",
    "HiveStandDiagnosis",
    "diagnosis_draft",
]


def diagnosis_draft(node: SwarmNode, reason: str) -> TaskGraphDraft:
    """Draft the one task that checks `node`'s reachability from the Hive Stand.

    Args:
        node: The device that was lost.
        reason: The short stable word its loss was reported with.

    Returns:
        A one-task graph: a Drone at low tempo, asked by the Queen, pinned to the Hive Stand.
    """
    objective = (
        f"Check reachability of device {node.cell_id} from the Hive Stand: DNS and route to its "
        "last address, whether other Swarm nodes are affected too.\n\n"
        f"Its link was reported lost as `{reason}`. Look, and change nothing: the name, the route "
        "and the port, and whether the Hive Stand's own network is the one at fault. Write what "
        f"you checked and what you found to `{FINDINGS_FILE}` in your scratch directory, and say "
        "in a sentence whether the fault looks like this end or that device.\n\n"
        f"{COMMAND_HINT}"
    )
    task = TaskDraft(
        key="diagnose",
        title=f"Check why device {node.cell_id} cannot be reached"[:120],
        objective=objective,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject=FINDINGS_FILE, argv=(), expected=None
            ),
        ),
        needs=TaskNeeds(tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.LOW)),
        clearance=HoneyClearance.C1,
        origin=RequestOrigin.QUEEN,
        capabilities=CEILING,
    )
    return TaskGraphDraft(tasks=(task,))


class HiveStandDiagnosis:
    """Ask the Hive Stand's Warden to check a lost device's reachability from this end.

    Owns mutable state (codingrules 8.5, documented): `_tasks` holds the diagnosis task of each
    device whose hold is under way.
    """

    def __init__(self, context_of: Callable[[], GateContext | None]) -> None:
        """Build the diagnosis over the gate's Queen, which exists only once the gate has started.

        Args:
            context_of: Returns the Queen the gate serves and her collaborators, or None before
                the gate has started (a hold cannot begin before then).
        """
        self._context_of = context_of
        self._tasks: dict[DeviceId, TaskId] = {}

    async def begin(self, node: SwarmNode, reason: str) -> None:
        """Create the diagnosis task for `node`'s hold, once.

        Args:
            node: The device that was lost.
            reason: The short stable word the loss was reported with.
        """
        context = self._context_of()
        if context is None or node.device_id in self._tasks:
            return
        deps = context.deps
        try:
            async with asyncio.timeout(DIAGNOSIS_TIMEOUT_S):
                [task] = await deps.chamber.submit(diagnosis_draft(node, reason))
                self._tasks[node.device_id] = task.id
                await record_event(
                    deps,
                    "queen.decided",
                    node.device_id,
                    reason="device_diagnosis",
                    detail="task_created",
                    task_id=task.id,
                )
        except _FAILURES as error:
            code = error.code if isinstance(error, HiveMindError) else type(error).__name__
            log.warning("swarm.diagnosis_not_created", device_id=node.device_id, reason=code)

    async def end(self, node: SwarmNode) -> None:
        """Cancel `node`'s diagnosis task if no Warden has started it; a running one finishes.

        Args:
            node: The device whose hold is over.
        """
        context, task_id = self._context_of(), self._tasks.pop(node.device_id, None)
        if context is None or task_id is None:
            return
        chamber = context.deps.chamber
        try:
            async with asyncio.timeout(DIAGNOSIS_TIMEOUT_S):
                if (await chamber.get(task_id)).status is TaskStatus.PENDING:
                    await chamber.cancel(task_id, CANCEL_REASON)
        except _FAILURES as error:
            code = error.code if isinstance(error, HiveMindError) else type(error).__name__
            log.warning("swarm.diagnosis_not_cancelled", device_id=node.device_id, reason=code)
