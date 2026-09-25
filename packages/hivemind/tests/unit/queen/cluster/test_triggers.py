"""Tests for hivemind.queen.cluster.triggers: cluster_if_down and every_bee_has_a_fallback.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/triggers.py (codingrules section 3). `check_cost_caps` is
    exercised through `run_cluster_tick` in test_tick.py, its one caller.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.triggers for the module under test.
"""

from __future__ import annotations

from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.cluster.triggers import cluster_if_down, every_bee_has_a_fallback
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from waggle.ids import TaskId


def _plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/done.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


async def _running_goal(*, probing: bool) -> tuple[QueenDeps, Queen, FakeLLMProvider, TaskId]:
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    deps, link, warden_end = make_queen_deps(
        fake_provider=provider,
        provider_lookup=(lambda _name: provider) if probing else None,
    )
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    return deps, queen, provider, goal_id


async def test_cluster_if_down_clusters_the_tasks_provider_on_one_down_reading() -> None:
    deps, queen, provider, goal_id = await _running_goal(probing=True)
    state = ClusterState()
    provider.set_outage(True)

    clustered = await cluster_if_down(deps, state, queen.wardens, goal_id)

    assert clustered is True
    assert state.clustered_providers == frozenset({"fake"})
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.PAUSED


async def test_cluster_if_down_leaves_a_provider_that_answers_alone() -> None:
    deps, queen, _provider, goal_id = await _running_goal(probing=True)
    state = ClusterState()

    clustered = await cluster_if_down(deps, state, queen.wardens, goal_id)

    assert clustered is False
    assert state.clustered_providers == frozenset()
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.RUNNING


async def test_cluster_if_down_does_nothing_without_a_provider_lookup() -> None:
    deps, queen, provider, goal_id = await _running_goal(probing=False)
    provider.set_outage(True)

    assert await cluster_if_down(deps, ClusterState(), queen.wardens, goal_id) is False


async def test_every_bee_has_a_fallback_is_false_for_a_grant_bound_to_one_provider() -> None:
    deps, _queen, _provider, _goal_id = await _running_goal(probing=True)

    # The test Hive's one live grant names sources on "fake" alone: nowhere to spill.
    assert every_bee_has_a_fallback("fake", deps, ClusterState()) is False
    # A provider no live grant names strands nobody.
    assert every_bee_has_a_fallback("elsewhere", deps, ClusterState()) is True
