"""Tests for hivemind.queen.swarm_gate.unreachable.hive_stand: the Hive Stand's Drone looks.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/unreachable/hive_stand.py (codingrules section 3). When
    a hold begins the Queen creates one task, with no planner, for the Hive Stand's own Warden: a
    low-tempo Drone she asked for herself, pinned to the Hive Stand, told to check the lost
    device's reachability from this end and change nothing. The hold ends it only if nobody has
    started it, and nothing that goes wrong with the task reaches the hold. The hold's own tests
    (`test_devices.py`) say the diagnosis is asked once per hold and that the decision still fires
    on its own clock.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.unreachable.hive_stand for the module under test.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for who investigates from this end.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from builders.swarm import GRACE_S, GateRig, place_on_device

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import CombShieldLevel, HoneyClearance, RequestOrigin
from hivemind.forage.tempo import AccuracyBar
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import GateContext
from hivemind.queen.swarm_gate.unreachable import HiveStandDiagnosis, diagnosis_draft
from hivemind.queen.swarm_gate.unreachable.hive_stand import (
    CANCEL_REASON,
    CEILING,
    COMMAND_HINT,
    FINDINGS_FILE,
)
from waggle.messages import PostconditionKind
from waggle.messages.task import WorkerRole


def _diagnosis(rig: GateRig, *, bound: bool = True) -> HiveStandDiagnosis:
    """A Hive Stand diagnosis over the rig's Queen, or over a gate that has not started."""
    context = GateContext(rig.queen, rig.queen_deps)
    return HiveStandDiagnosis(lambda: context if bound else None)


async def _tasks(rig: GateRig) -> list[object]:
    """Every task in the Queen's Brood Chamber, oldest first."""
    return list(await rig.queen_deps.chamber.list(TaskFilter()))


async def test_the_task_is_a_low_tempo_drone_the_queen_asked_for_pinned_to_the_hive_stand(
    rig: GateRig,
) -> None:
    node = await rig.enrol()

    [task] = diagnosis_draft(node, "closed").tasks

    assert task.role is WorkerRole.DRONE
    assert task.origin is RequestOrigin.QUEEN and task.clearance is HoneyClearance.C1
    assert task.needs.tempo.accuracy is AccuracyBar.LOW
    assert set(task.capabilities or ()) == set(CEILING)  # Carried as the sorted set it is.
    assert "cell:hive_stand" in CEILING  # The one Cell it can be placed on.
    assert not any(c.startswith(("cell:real", "cell:virtual")) for c in CEILING)
    [criterion] = task.acceptance
    assert criterion.kind is PostconditionKind.FILE_EXISTS and criterion.subject == FINDINGS_FILE


async def test_the_objective_names_the_device_and_what_to_check_and_nothing_to_find_it_by(
    rig: GateRig,
) -> None:
    node = await rig.enrol()

    [task] = diagnosis_draft(node, "missed_heartbeats").tasks

    assert task.objective.startswith(
        f"Check reachability of device {node.cell_id} from the Hive Stand: DNS and route to its "
        "last address, whether other Swarm nodes are affected too."
    )
    assert "`missed_heartbeats`" in task.objective  # The word the loss was reported with.
    assert "change nothing" in task.objective.lower()
    # A diagnosis learns a node and a reason word, never an address, a key or a path.
    assert str(node.device_id) not in task.objective
    # The command tool says only whether a command worked: the Drone is told to write its output
    # to a file and read it back, with every check ending in success.
    assert COMMAND_HINT in task.objective and "always succeeds" in task.objective


def test_the_ceiling_leaves_the_tools_a_role_lists_and_nothing_that_asks_or_reaches_out() -> None:
    # A Drone's role lists its tools as `tool:*` and a ceiling keeps only what one of its own
    # entries covers, so naming `tool:run_command` and the like would leave it no tool at all.
    assert "tool:*" in CEILING
    # What the other tools ask for is left out: a question to a human, a network of its own,
    # Honey, Cell Wax, spend and a way to ask the Hive for a tool.
    left_out = ("question", "net", "honey", "wax", "spend", "forage", "tool:request")
    assert not any(c.startswith(left_out) for c in CEILING)


async def test_a_hold_that_begins_creates_the_one_task_and_says_so_on_the_trail(
    rig: GateRig,
) -> None:
    node = await rig.enrol()
    diagnosis = _diagnosis(rig)

    await diagnosis.begin(node, "closed")
    await diagnosis.begin(node, "closed")  # The same hold, told again.

    [task] = await rig.queen_deps.chamber.list(TaskFilter())
    assert task.status is TaskStatus.PENDING
    assert task.spec.origin is RequestOrigin.QUEEN and task.spec.role is WorkerRole.DRONE
    decided = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    [event] = [e for e in decided if e.payload.get("reason") == "device_diagnosis"]
    assert event.payload["detail"] == "task_created" and event.payload["task_id"] == task.id


async def test_a_task_nobody_started_is_cancelled_when_the_hold_ends(rig: GateRig) -> None:
    node = await rig.enrol()
    diagnosis = _diagnosis(rig)
    await diagnosis.begin(node, "closed")

    await diagnosis.end(node)

    [task] = await rig.queen_deps.chamber.list(TaskFilter())
    assert task.status is TaskStatus.CANCELLED
    assert task.outcome is not None and task.outcome.summary == CANCEL_REASON


async def test_a_task_a_warden_has_started_is_left_to_finish(rig: GateRig) -> None:
    node = await rig.enrol()
    diagnosis = _diagnosis(rig)
    await diagnosis.begin(node, "closed")
    [task] = await rig.queen_deps.chamber.list(TaskFilter())
    await rig.queen_deps.chamber.assign(
        task.id, node.warden_id, node.cell_id, "Placed.", bound_tier=CombShieldLevel.MEADOW
    )

    await diagnosis.end(node)

    [after] = await rig.queen_deps.chamber.list(TaskFilter())
    assert after.status is TaskStatus.ASSIGNED


async def test_a_gate_that_has_not_started_creates_nothing(rig: GateRig) -> None:
    node = await rig.enrol()
    diagnosis = _diagnosis(rig, bound=False)

    await diagnosis.begin(node, "closed")
    await diagnosis.end(node)

    assert await _tasks(rig) == []


async def test_a_task_that_cannot_be_created_is_logged_and_never_reaches_the_hold(
    rig: GateRig,
) -> None:
    node = await rig.enrol()
    diagnosis = _diagnosis(rig)

    with patch.object(
        rig.queen_deps.chamber, "submit", side_effect=sqlite3.OperationalError("disk full")
    ):
        await diagnosis.begin(node, "closed")  # Must not raise.

    assert await _tasks(rig) == []
    await diagnosis.end(node)  # Nothing was created, so there is nothing to cancel.


async def test_the_holds_own_clock_still_decides_whatever_the_diagnosis_does(
    rig: GateRig,
) -> None:
    async with rig.serving():
        # A task on the device, so the hold has someone waiting on the diagnosis's answer.
        node = (await place_on_device(rig)).node
        rig.unreachable.use_diagnosis(_diagnosis(rig))
        rig.unreachable.note(node.device_id, "missed_heartbeats")  # A loss somebody saw.

        await rig.unreachable.lost(node)
        rig.clock.advance(GRACE_S + 1.0)
        await rig.unreachable.lost(node)

        tasks = await rig.queen_deps.chamber.list(TaskFilter())
        [task] = [task for task in tasks if task.spec.origin is RequestOrigin.QUEEN]
        assert task.spec.origin is RequestOrigin.QUEEN  # The task exists ...
        decided = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
        assert "unreachable_decision" in {str(e.payload.get("reason")) for e in decided}
