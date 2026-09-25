"""Tests for hivemind.queen.cluster.tick: run_cluster_tick and awake_available.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/tick.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.tick for the module under test.
"""

from __future__ import annotations

from builders.cells import make_cell
from builders.forage import make_grant
from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CellKind, HoneyClearance
from hivemind.forage import GrantState, ModelSlot
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import CellEvent, TrailQuery
from hivemind.queen.cluster.health import (
    DEFAULT_HEALTHY_PROBE_INTERVAL_S,
    DEFAULT_INITIAL_BACKOFF_S,
)
from hivemind.queen.cluster.orders import ClusterOrder, InMemoryOrderStore, OrderKind, new_order_id
from hivemind.queen.cluster.protocol import cluster
from hivemind.queen.cluster.tick import awake_available, run_cluster_tick, run_release_tick
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from waggle.clock import FakeClock
from waggle.ids import CellId, TaskId, new_cell_id, new_event_id, new_hive_id, new_node_id
from waggle.messages.supervision import InterventionAction


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
    await queen.attach_warden(link)
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
    await queen.attach_warden(link)
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
    await queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)
    provider.set_outage(True)  # Still down.

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset({"fake"})


# ──────────────────────────────────────────────────────────────────────────────
# The outage trigger: a bound provider that reads DOWN twice, with no fallback, clusters itself
# ──────────────────────────────────────────────────────────────────────────────


async def _running_goal_with_probing(
    clock: FakeClock,
) -> tuple[QueenDeps, Queen, FakeLLMProvider, TaskId]:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(
        clock=clock,
        fake_provider=provider,
        orders=InMemoryOrderStore(),
        provider_lookup=lambda _name: provider,
    )
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    return deps, queen, provider, goal_id


async def test_run_cluster_tick_clusters_a_bound_provider_after_two_down_probes() -> None:
    """Roadmap 4.9's first trigger: DOWN with no fallback within Forage pauses the bees on it."""
    clock = FakeClock()
    deps, queen, provider, goal_id = await _running_goal_with_probing(clock)
    state = ClusterState()
    provider.set_outage(True)

    await run_cluster_tick(deps, state, queen.wardens)  # First DOWN reading: a blip, not yet.
    assert state.clustered_providers == frozenset()
    clock.advance(DEFAULT_INITIAL_BACKOFF_S)
    await run_cluster_tick(deps, state, queen.wardens)  # Second in a row: an outage.

    assert state.clustered_providers == frozenset({"fake"})
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.PAUSED
    clustered = await deps.trail.query(TrailQuery(kind="queen.clustered"))
    assert [event.payload.get("cause") for event in clustered] == ["provider_down"]


async def test_run_cluster_tick_probes_a_healthy_bound_provider_only_at_the_steady_cadence() -> (
    None
):
    clock = FakeClock()
    deps, queen, _provider, goal_id = await _running_goal_with_probing(clock)
    state = ClusterState()

    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset()
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.RUNNING
    # Probed once, then not again until the steady interval has passed: no GET per tick.
    assert not deps.health_poller.is_due("fake", clock.now())
    clock.advance(DEFAULT_HEALTHY_PROBE_INTERVAL_S)
    assert deps.health_poller.is_due("fake", clock.now())


async def test_run_cluster_tick_never_clusters_on_a_single_down_reading() -> None:
    clock = FakeClock()
    deps, queen, provider, goal_id = await _running_goal_with_probing(clock)
    state = ClusterState()
    provider.set_outage(True)

    await run_cluster_tick(deps, state, queen.wardens)
    clock.advance(DEFAULT_INITIAL_BACKOFF_S)
    provider.set_outage(False)  # Back before the confirming probe: a blip.
    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset()
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.RUNNING
    assert deps.health_poller.failed_probes("fake") == 0


async def test_run_cluster_tick_resumes_a_recovered_provider_it_clustered_itself() -> None:
    clock = FakeClock()
    deps, queen, provider, goal_id = await _running_goal_with_probing(clock)
    state = ClusterState()
    provider.set_outage(True)
    await run_cluster_tick(deps, state, queen.wardens)
    clock.advance(DEFAULT_INITIAL_BACKOFF_S)
    await run_cluster_tick(deps, state, queen.wardens)
    assert state.clustered_providers == frozenset({"fake"})

    provider.set_outage(False)
    clock.advance(DEFAULT_INITIAL_BACKOFF_S * 2)  # Past the second backoff step.
    await run_cluster_tick(deps, state, queen.wardens)

    assert state.clustered_providers == frozenset()
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.RUNNING


# ──────────────────────────────────────────────────────────────────────────────
# run_release_tick (roadmap step 5.13): drains RELEASE orders, revokes the lease's Cell's grants.
# ──────────────────────────────────────────────────────────────────────────────


async def _record_leased(
    deps: QueenDeps, clock: FakeClock, *, cell_id: CellId, lease_id: str
) -> None:
    """Write one `cell.leased` event so `run_release_tick` can resolve `lease_id` to `cell_id`."""
    event = CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="cell.leased",
        subject_id=cell_id,
        payload={
            "lease_id": lease_id,
            "holder": "warden-1",
            "task_id": None,
            "access_level": "FULL",
            "comb_shield": "MEADOW",
        },
    )
    await deps.trail.record(event)


async def test_run_release_tick_revokes_the_leased_cells_live_grants_and_marks_handled() -> None:
    clock = FakeClock()
    orders = InMemoryOrderStore()
    deps, _link, _warden_end = make_queen_deps(clock=clock, orders=orders)
    cell_id = new_cell_id(clock)
    await _record_leased(deps, clock, cell_id=cell_id, lease_id="lease-1")
    grant = make_grant(state=GrantState.ACTIVE, clock=clock, cell_id=cell_id)
    await deps.ledger.record_grant(grant)
    await orders.put_order(
        ClusterOrder(
            id=new_order_id(clock),
            kind=OrderKind.RELEASE,
            provider=None,
            requested_at=clock.now(),
            lease_id="lease-1",
        )
    )

    # No attached Warden owns cell_id (a fresh, unrelated one): the grant revoke still runs, and
    # there is simply nothing to send the Intervene(RELEASE_LEASE) to.
    handled = await run_release_tick(deps, ())

    assert len(handled) == 1
    assert deps.ledger.live_grants() == ()
    assert await orders.pending() == ()
    revoked = await deps.trail.query(TrailQuery(kind="forage.revoked"))
    assert [event.subject_id for event in revoked] == [grant.id]


async def test_run_release_tick_sends_release_lease_to_the_owning_warden() -> None:
    """The core of roadmap step 5.13: the attached Warden that owns the leased Cell is told."""
    clock = FakeClock()
    orders = InMemoryOrderStore()
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    deps, link, warden_end = make_queen_deps(clock=clock, orders=orders, cell=cell)
    await _record_leased(deps, clock, cell_id=cell.id, lease_id="lease-1")
    await orders.put_order(
        ClusterOrder(
            id=new_order_id(clock),
            kind=OrderKind.RELEASE,
            provider=None,
            requested_at=clock.now(),
            lease_id="lease-1",
        )
    )

    handled = await run_release_tick(deps, (link,))
    intervene = await warden_end.wait_for_intervene()

    assert len(handled) == 1
    assert intervene.action is InterventionAction.RELEASE_LEASE
    assert intervene.subject is None


async def test_run_release_tick_on_an_unknown_lease_id_still_marks_handled() -> None:
    clock = FakeClock()
    orders = InMemoryOrderStore()
    deps, _link, _warden_end = make_queen_deps(clock=clock, orders=orders)
    await orders.put_order(
        ClusterOrder(
            id=new_order_id(clock),
            kind=OrderKind.RELEASE,
            provider=None,
            requested_at=clock.now(),
            lease_id="never-seen",
        )
    )

    handled = await run_release_tick(deps, ())

    assert len(handled) == 1
    assert await orders.pending() == ()


async def test_run_release_tick_never_touches_a_cluster_or_wake_order() -> None:
    clock = FakeClock()
    orders = InMemoryOrderStore()
    deps, _link, _warden_end = make_queen_deps(clock=clock, orders=orders)
    order = ClusterOrder(
        id=new_order_id(clock), kind=OrderKind.CLUSTER, provider="fake", requested_at=clock.now()
    )
    await orders.put_order(order)

    handled = await run_release_tick(deps, ())

    assert handled == ()
    pending = await orders.pending()
    assert [item.id for item in pending] == [order.id]  # Left for run_cluster_tick, untouched.
