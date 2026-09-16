"""Tests for hivemind.queen.cluster.tick: run_cluster_tick and awake_available.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/tick.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.tick for the module under test.
"""

from __future__ import annotations

from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.forage import ModelSlot
from hivemind.llm import FakeLLMProvider
from hivemind.queen.cluster.orders import ClusterOrder, InMemoryOrderStore, OrderKind, new_order_id
from hivemind.queen.cluster.protocol import cluster
from hivemind.queen.cluster.tick import awake_available, run_cluster_tick
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from waggle.ids import TaskId


def _single_task_plan(goal: str) -> dict[str, object]:
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


async def _running_goal() -> tuple[QueenDeps, Queen, WardenEnd, TaskId]:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    orders = InMemoryOrderStore()
    deps, link, warden_end = make_queen_deps(fake_provider=provider, orders=orders)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    return deps, queen, warden_end, goal_id


async def test_run_cluster_tick_never_calls_the_provider_with_nothing_pending() -> None:
    deps, queen, _warden_end, _goal_id = await _running_goal()
    state = ClusterState()
    provider = deps.bound_for(ModelSlot.WORKER).provider
    calls_before = len(provider.calls)  # type: ignore[attr-defined]

    await run_cluster_tick(deps, state, queen.wardens)

    assert len(provider.calls) == calls_before  # type: ignore[attr-defined]


async def test_run_cluster_tick_acts_on_a_pending_cluster_order() -> None:
    deps, queen, _warden_end, goal_id = await _running_goal()
    state = ClusterState()
    order = ClusterOrder(
        id=new_order_id(deps.clock),
        kind=OrderKind.CLUSTER,
        provider="fake",
        requested_at=deps.clock.now(),
    )
    await deps.orders.put_order(order)

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset({"fake"})
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.PAUSED
    pending = await deps.orders.pending()
    assert pending == ()  # Consumed once: mark_handled ran in the same call.


async def test_run_cluster_tick_acts_on_a_pending_wake_order() -> None:
    deps, queen, _warden_end, goal_id = await _running_goal()
    state = ClusterState()
    state.mark_clustered("fake")
    await deps.chamber.pause(goal_id, "test setup")
    order = ClusterOrder(
        id=new_order_id(deps.clock),
        kind=OrderKind.WAKE,
        provider="fake",
        requested_at=deps.clock.now(),
    )
    await deps.orders.put_order(order)

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset()
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING


async def test_run_cluster_tick_wake_with_no_provider_resumes_every_clustered_one() -> None:
    deps, queen, _warden_end, goal_id = await _running_goal()
    state = ClusterState()
    state.mark_clustered("fake")
    await deps.chamber.pause(goal_id, "test setup")
    order = ClusterOrder(
        id=new_order_id(deps.clock),
        kind=OrderKind.WAKE,
        provider=None,
        requested_at=deps.clock.now(),
    )
    await deps.orders.put_order(order)

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset()


async def test_awake_available_is_true_while_the_queen_provider_is_not_clustered() -> None:
    deps, _queen, _warden_end, _goal_id = await _running_goal()
    state = ClusterState()

    assert awake_available(state, deps) is True


async def test_awake_available_is_false_once_the_queens_own_provider_is_clustered() -> None:
    deps, _queen, _warden_end, _goal_id = await _running_goal()
    state = ClusterState()
    queen_provider = next(
        b.provider for b in deps.bindings if b.key == ModelSlot.QUEEN.manifest_key
    )

    state.mark_clustered(queen_provider)

    assert awake_available(state, deps) is False


async def test_run_cluster_tick_probes_a_clustered_provider_and_resumes_once_healthy() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    orders = InMemoryOrderStore()
    deps, link, warden_end = make_queen_deps(
        fake_provider=provider, orders=orders, provider_lookup=lambda _name: provider
    )
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)
    provider.set_outage(False)  # The provider recovers.

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset()
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING


async def test_run_cluster_tick_leaves_a_still_down_provider_clustered() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    orders = InMemoryOrderStore()
    deps, link, warden_end = make_queen_deps(
        fake_provider=provider, orders=orders, provider_lookup=lambda _name: provider
    )
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)
    provider.set_outage(True)  # Still down.

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset({"fake"})
