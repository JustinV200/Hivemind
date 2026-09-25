"""Tests for a goal's capability set on the Queen's side: carried, placed under, refused by.

Roadmap step 10.3 (ADR-0039, "A goal carries a ceiling"): `Queen.submit_goal` threads the
submitter's set to every planned task, the dispatcher sends it (and the task's network scopes) on
the Waggle 1.8 `task.assign`, and a goal whose set admits no Cell fails placement with one
`guard.denied` per capability it lacked, once: the task is cancelled with the reason, because a
goal's set never changes and a PENDING task would be refused again on every tick.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py and .dispatcher (codingrules section 3); split by feature
    (14.2), like test_queen_dispatch.py and its siblings.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.goal_submission and hivemind.queen.dispatcher.ready for the code under test.
"""

from __future__ import annotations

from builders.cells import make_capabilities, make_cell
from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import CellKind, HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.queen import Queen

_SCOPE = "api.example.com"
# Everything the builder's one Real Cell (not the Hive Stand) needs of a goal, and a Drone's tools.
_GOAL = ("cell:real:*", "cell:comb_shield:*", "llm:*", "tool:*", "fs:read:**", "net:*")


def _plan(goal: str) -> dict[str, object]:
    """One task that needs `_SCOPE` on the network."""
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Fetch the data",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": "data.json", "argv": [], "expected": None}
                ],
                "needs": {"network_scopes": [_SCOPE]},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def _networked_queen() -> tuple[Queen, WardenLink, WardenEnd]:
    """A Queen over a Real Cell that can reach `_SCOPE`, planning `_plan`."""
    cell = make_cell(kind=CellKind.REAL, capabilities=make_capabilities(network_scopes=(_SCOPE,)))
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, cell=cell)
    return Queen(deps), link, warden_end


async def test_the_goal_set_and_network_scopes_travel_to_the_task_and_its_assignment() -> None:
    queen, link, warden_end = _networked_queen()
    await queen.attach_warden(link)

    goal_id = await queen.submit_goal("Fetch.", clearance=HoneyClearance.C1, capabilities=_GOAL)

    assignment = await warden_end.wait_for_assignment()
    task = await queen._deps.chamber.get(goal_id)
    assert task.spec.capabilities == tuple(sorted(_GOAL))
    assert assignment.capabilities == task.spec.capabilities
    assert assignment.network_scopes == (_SCOPE,)
    await warden_end.close()


async def test_the_operators_own_goal_sends_no_ceiling_on_the_wire() -> None:
    queen, link, warden_end = _networked_queen()
    await queen.attach_warden(link)

    await queen.submit_goal("Fetch.", clearance=HoneyClearance.C1)

    assignment = await warden_end.wait_for_assignment()
    assert assignment.capabilities is None
    assert assignment.network_scopes == (_SCOPE,)
    await warden_end.close()


async def test_a_goal_set_admitting_no_cell_fails_placement_with_a_guard_denial() -> None:
    queen, link, warden_end = _networked_queen()
    await queen.attach_warden(link)

    goal_id = await queen.submit_goal(
        "Fetch.", clearance=HoneyClearance.C1, capabilities=("tool:*", "cell:comb_shield:*")
    )

    deps = queen._deps
    [task] = await deps.chamber.list(TaskFilter(goal_id=goal_id))
    assert task.status is TaskStatus.CANCELLED  # Nothing could ever run it; nothing was assigned.
    assert task.outcome is not None
    assert f"lacks cell:real:{link.cell.id}" in task.outcome.summary
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "placement"
    assert denial.payload["principal_kind"] == "queen"
    assert denial.payload["capability"] == f"cell:real:{link.cell.id}"
    [decided] = await deps.trail.query(TrailQuery(kind="queen.decided"))
    assert decided.payload["reason"] == "placement_failed"
    assert "goal lacks cell:real:" in str(decided.payload["detail"])
    await warden_end.close()


async def test_an_unplaceable_goal_is_refused_once_not_on_every_tick() -> None:
    # Before the fix the task stayed PENDING and every dispatch pass (every Queen tick) recorded
    # the same queen.decided and guard.denied again, flooding the trail and the Guard's counts.
    queen, link, warden_end = _networked_queen()
    await queen.attach_warden(link)
    await queen.submit_goal(
        "Fetch.", clearance=HoneyClearance.C1, capabilities=("tool:*", "cell:comb_shield:*")
    )
    deps = queen._deps

    await dispatch_ready(deps, (link,))
    await dispatch_ready(deps, (link,))

    assert len(await deps.trail.query(TrailQuery(kind="guard.denied"))) == 1
    assert len(await deps.trail.query(TrailQuery(kind="queen.decided"))) == 1
    await warden_end.close()
