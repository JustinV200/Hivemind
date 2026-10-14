"""Build the facts of the Queen's own rung decision, and the Hive that gives her them.

The decision to raise a device a rung or take one back (`decide_rung`) is a pure function of one
value, `RungFacts`: the device, the gate's numbers, the Hive Stand's relief pressure, the seat
pressure, whether a task needs to outlive its link, whether the device is idle, and what the Queen
remembers of her own moves on it. `make_rung_facts` builds a device the Queen could colonize if a
reason held and no reason does: a Level 0 device that reports Python 3.12, a modest host and the
access level she may work at, with nothing under pressure. A test names the one fact under test, a
level, a ceiling, a pressure or a mark, and everything else stays quiet. `survival_task` submits a
task to the Brood Chamber that must outlive its link, placed on a device or pinned to one.

The rest builds the Hive those facts are read from, for the tests that run the evaluator against a
gate rig. `report_stand` has the Hive Stand's own Warden report a host of a chosen size, `run_on`
puts bees to work on a device, `make_evaluator` builds the evaluator over the rig's colonizer,
`run_pass` runs one pass of the Queen's own decisions the way the gate does, and `make_reconciler`
builds the gate's reconciler with that evaluator in it, for the tests of how the pass reaches it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/swarm_gate.

Key invariants:
    - The time is a fact: `NOW` is the FakeClock's start, and a test that needs another moment adds
      a `timedelta` to it (or advances the rig's clock); nothing here reads a clock but the rig's.
    - The defaults give a device that no reason moves, so a test that sets none asserts "nothing".
    - A pass reads the registry afresh, so a device the previous pass moved is judged where it is.

See Also:
    - hivemind.queen.swarm_gate.rungs.decide for the decision under test.
"""

from __future__ import annotations

from datetime import datetime

from builders.forage import make_capacity, make_footprint, make_host_capacity
from builders.swarm.gate import GateRig, gate_terms
from builders.swarm.parties import make_swarm_node
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CombShieldLevel, TaskNeeds, hive_stand_cell_id
from hivemind.queen import QueenDeps
from hivemind.queen.swarm_gate import DeviceReconciler, GateContext, ReconcileDeps
from hivemind.queen.swarm_gate.rungs import Memory, RungEvaluator, RungFacts
from hivemind.swarm import SwarmNode
from waggle.clock import FakeClock
from waggle.ids import CellId, DeviceId, WardenId

__all__ = [
    "NOW",
    "make_evaluator",
    "make_reconciler",
    "make_rung_facts",
    "report_stand",
    "run_on",
    "run_pass",
    "survival_task",
]

NOW: datetime = FakeClock().now()  # The moment every decision test is judged at.


def make_rung_facts(node: SwarmNode | None = None, **overrides: object) -> RungFacts:
    """Build the facts for a device the Queen could move if a reason held; none holds.

    Args:
        node: The device; a Level 0 device that can carry a Warden when omitted.
        **overrides: Fields of `RungFacts` to replace (the node's own fields are replaced by
            building the node, not here).

    Returns:
        The facts.
    """
    fields: dict[str, object] = {
        "node": node if node is not None else make_swarm_node(),
        "terms": gate_terms(),
        "footprint": make_footprint(),
        "now": NOW,
        "relief": 0.0,
        "seats": 0.0,
        "survives": False,
        "declared": (),
        "idle": False,
        "memory": Memory(),
    }
    fields.update(overrides)
    return RungFacts(**fields)  # type: ignore[arg-type]  # Every field is named above.


async def survival_task(
    deps: QueenDeps,
    *,
    survives: bool = True,
    on: tuple[WardenId, CellId] | None = None,
    pinned_to: CellId | None = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    """Submit a task to the Brood Chamber, optionally placed on a Cell or pinned to one.

    Args:
        deps: The Queen's collaborators; the chamber is written.
        survives: Whether the task must survive the link to the Queen being lost.
        on: The Warden and Cell to place it on (assigned, and started when `status` is RUNNING).
        pinned_to: A Real Cell the goal's ceiling lets it run on and nowhere else.
        status: PENDING (the default), ASSIGNED or RUNNING; a placed task needs `on`.

    Returns:
        The task as the chamber holds it.
    """
    capabilities = None if pinned_to is None else (f"cell:real:{pinned_to}",)
    draft = make_graph_draft({"root": ()})
    task_draft = draft.tasks[0].model_copy(
        update={"needs": TaskNeeds(survives_link_loss=survives), "capabilities": capabilities}
    )
    [task] = await deps.chamber.submit(draft.model_copy(update={"tasks": (task_draft,)}))
    if on is not None and status is not TaskStatus.PENDING:
        warden_id, cell_id = on
        tier = CombShieldLevel.MEADOW
        await deps.chamber.assign(task.id, warden_id, cell_id, "Placed.", bound_tier=tier)
        if status is TaskStatus.RUNNING:
            return await deps.chamber.start(task.id)
    return await deps.chamber.get(task.id)


async def report_stand(rig: GateRig, **host: object) -> None:
    """Have the Hive Stand's own Warden report a host of the given shape to the Forage ledger.

    Args:
        rig: The gate rig, whose Queen's ledger is written.
        **host: Fields of the reported `HostCapacity` to replace (8 cores and 16 GiB by default).
    """
    stand = hive_stand_cell_id(rig.queen_deps.identity.node_id)
    capacity = make_capacity(host=make_host_capacity(**host))
    await rig.queen_deps.ledger.report_capacity(stand, capacity)


async def run_on(
    rig: GateRig, node: SwarmNode, *, count: int = 1, survives: bool = False
) -> list[Task]:
    """Put `count` tasks RUNNING on `node`'s Cell, each a Drone that costs one bee.

    Args:
        rig: The gate rig, whose Queen's Brood Chamber is written.
        node: The device whose Cell they run on.
        count: How many.
        survives: Whether each must survive the link to the Queen being lost.

    Returns:
        The tasks, RUNNING.
    """
    return [
        await survival_task(
            rig.queen_deps,
            survives=survives,
            on=(node.warden_id, node.cell_id),
            status=TaskStatus.RUNNING,
        )
        for _ in range(count)
    ]


def make_evaluator(rig: GateRig) -> RungEvaluator:
    """Build the Queen's rung evaluator over the rig's colonizer and hub, bound to the rig's Queen.

    Args:
        rig: The gate rig; a test that expects a flow to run opens `rig.serving()` first.

    Returns:
        The evaluator, working to the rig's terms on the rig's clock.
    """
    evaluator = RungEvaluator(rig.colonizer, rig.hive.hub, rig.clock, rig.terms)
    evaluator.bind(GateContext(rig.queen, rig.queen_deps))
    return evaluator


async def run_pass(rig: GateRig, evaluator: RungEvaluator) -> dict[DeviceId, bool]:
    """Run one reconcile pass of the Queen's own decisions over every enrolled device.

    Args:
        rig: The gate rig, whose registry is read afresh.
        evaluator: The evaluator under test.

    Returns:
        For each device, in the order the pass met them (heaviest on the Hive Stand first),
        whether a flow owns it afterwards.
    """
    nodes = await evaluator.begin_pass(await rig.hive.registry.nodes())
    return {node.device_id: await evaluator.evaluate(node) for node in nodes}


def make_reconciler(rig: GateRig, evaluator: RungEvaluator) -> DeviceReconciler:
    """Build the gate's reconciler over the rig's parts with `evaluator` in it, bound to the Queen.

    Args:
        rig: The gate rig; a test that expects a flow to run opens `rig.serving()` first.
        evaluator: The Queen's rung evaluator the pass is to hand devices to.

    Returns:
        The reconciler, whose `sweep` runs a whole pass.
    """
    deps = ReconcileDeps(
        rig.hive.registry, rig.listener, rig.wardens, rig.unreachable, rig.colonizer, evaluator
    )
    reconciler = DeviceReconciler(deps)
    reconciler.bind(GateContext(rig.queen, rig.queen_deps))
    return reconciler
