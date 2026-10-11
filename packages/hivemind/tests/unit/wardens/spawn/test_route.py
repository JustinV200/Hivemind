"""Tests for hivemind.wardens.spawn.route: the Warden's routing step for one sub-bee.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/route.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.route for the module under test.
    - hivemind.llm.routing for the pure policy these tests drive through a Warden's deps.
"""

from __future__ import annotations

from typing import Any

from builders.routing import make_chain_source
from builders.warden_routing import routing_overrides
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import make_assignment

from hivemind.forage import Abundance, Effort, ModelSlot
from hivemind.llm.routing import PassOverCause, RoutingRules
from hivemind.pheromone import TrailQuery
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn.route import (
    RouteScope,
    bind_routed,
    pick_binding,
    record_routing,
    route_rebind,
    route_spawn,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_worker_id
from waggle.messages.forage import ForageRequestKind
from waggle.messages.labels import AccuracyBar, Tempo
from waggle.messages.task import ExoskeletonNeed


def _scope(
    deps: WardenDeps, *, accuracy: AccuracyBar = AccuracyBar.NORMAL, **assignment: object
) -> RouteScope:
    """Build a scope for a worker task at `accuracy`, with any other assignment fields."""
    task = make_assignment(
        clock=deps.clock, tempo=Tempo(latency_budget_s=None, accuracy=accuracy), **assignment
    )
    return RouteScope(deps, new_cell_id(deps.clock), None, task, task.grant_id)


def _two_grades(clock: FakeClock) -> dict[str, Any]:
    """A grade-1 primary (`small`) falling back to a grade-4 `strong`, both free.

    The operator allows the weak primary (`[llm.floors] worker = 1`); only a tempo that asks for
    more than the floor then passes it over.
    """
    sources = [make_chain_source("small", grade=1), make_chain_source("strong", grade=4)]
    return routing_overrides(clock, sources, rules=RoutingRules(floors={ModelSlot.WORKER: 1}))


def test_route_spawn_does_nothing_without_a_map_rules_or_a_row_for_the_slot() -> None:
    clock = FakeClock()
    wired = _two_grades(clock)
    # Dropping any one of the three leaves nothing to route by; no rows is an empty tuple.
    for dropped, empty in (("map", None), ("routing_rules", None), ("bindings", ())):
        deps, _queen, _warden = make_warden_deps(clock, **{**wired, dropped: empty})
        scope = _scope(deps, accuracy=AccuracyBar.CRITICAL)
        assert route_spawn(scope) is None
        assert route_rebind(scope, "worker") is None
        # With nothing to route by, the slot's own key is bound, as before phase 8.
        assert pick_binding(scope, None) == (ModelSlot.WORKER.manifest_key, None)


def test_a_critical_task_passes_over_a_grade_one_primary_for_its_grade_four_fallback() -> None:
    clock = FakeClock()
    deps, _queen, _warden = make_warden_deps(clock, **_two_grades(clock))

    routed = route_spawn(_scope(deps, accuracy=AccuracyBar.CRITICAL))

    assert routed is not None
    assert routed.key == "strong"
    assert routed.decision.order == ("strong", "worker")
    assert routed.effort is Effort.HIGH  # CRITICAL asks the strongest model to think hardest.
    [passed] = routed.decision.passed_over
    assert (passed.key, passed.cause) == ("worker", PassOverCause.GRADE_BELOW_FLOOR)


def test_a_low_tempo_task_keeps_the_primary_at_low_effort() -> None:
    clock = FakeClock()
    deps, _queen, _warden = make_warden_deps(clock, **_two_grades(clock))

    routed = route_spawn(_scope(deps, accuracy=AccuracyBar.LOW))

    assert routed is not None
    assert (routed.key, routed.effort) == ("worker", Effort.LOW)
    assert routed.decision.order == ("worker", "strong")


def test_a_task_that_needs_a_screen_is_routed_to_a_source_that_sees() -> None:
    clock = FakeClock()
    sources = [
        make_chain_source("small", grade=3),
        make_chain_source("strong", grade=3, capabilities=("vision",)),
    ]
    deps, _queen, _warden = make_warden_deps(clock, **routing_overrides(clock, sources))

    blind = route_spawn(_scope(deps))
    sighted = route_spawn(_scope(deps, exoskeleton=ExoskeletonNeed()))

    assert blind is not None and blind.key == "worker"  # No screen: the primary stays first.
    assert sighted is not None and sighted.key == "strong"
    assert sighted.decision.passed_over[0].cause is PassOverCause.CAPABILITY_MISSING


def test_bind_routed_follows_the_routed_order_with_the_routed_effort() -> None:
    clock = FakeClock()
    deps, _queen, _warden = make_warden_deps(clock, **_two_grades(clock))
    routed = route_spawn(_scope(deps, accuracy=AccuracyBar.CRITICAL))
    assert routed is not None

    bound = bind_routed(deps.rebind, routed.key, routed)

    # The manifest chain is worker -> strong; the routed one is strong -> worker, and neither
    # link keeps the other's manifest-order fallback.
    assert [link.binding for link in bound.chain()] == ["strong", "worker"]
    assert bound.fallback is not None and bound.fallback.fallback is None
    assert bound.effort is Effort.HIGH
    assert bound.fallback.effort is Effort.MEDIUM  # Only the head link takes the routed effort.


def test_bind_routed_without_a_decision_binds_the_key_as_given() -> None:
    clock = FakeClock()
    deps, _queen, _warden = make_warden_deps(clock, **_two_grades(clock))

    bound = bind_routed(deps.rebind, "strong", None)

    assert [link.binding for link in bound.chain()] == ["strong"]


async def test_a_non_primary_choice_records_one_llm_fallback_with_its_evidence() -> None:
    clock = FakeClock()
    deps, _queen, _warden = make_warden_deps(clock, **_two_grades(clock))
    scope = _scope(deps, accuracy=AccuracyBar.CRITICAL)
    routed = route_spawn(scope)
    assert routed is not None

    await record_routing(scope, routed, new_worker_id(clock))

    [event] = await deps.trail.query(TrailQuery(kind="llm.fallback"))
    assert event.payload["slot"] == "WORKER"
    assert (event.payload["from_key"], event.payload["to_key"]) == ("worker", "strong")
    assert event.payload["provider"] == "strong"
    assert event.payload["passed_over"] == [{"key": "worker", "cause": "GRADE_BELOW_FLOOR"}]
    assert event.payload["reason"] == routed.decision.reason.render()
    assert event.payload["task_id"] == scope.assignment.task_id
    assert event.payload["goal_id"] == scope.assignment.goal_id


async def test_keeping_the_primary_records_no_llm_fallback() -> None:
    clock = FakeClock()
    deps, _queen, _warden = make_warden_deps(clock, **_two_grades(clock))
    scope = _scope(deps, accuracy=AccuracyBar.LOW)
    routed = route_spawn(scope)
    assert routed is not None and routed.key == "worker"

    await record_routing(scope, routed, new_worker_id(clock))

    assert await deps.trail.query(TrailQuery(kind="llm.fallback")) == ()


async def test_a_seat_shortfall_files_one_shared_seats_request_and_does_not_block() -> None:
    clock = FakeClock()
    starved = make_chain_source("small", grade=3, abundance=Abundance(seats_free=0))
    overrides = routing_overrides(clock, [starved], keys=("small",))
    deps, queen_end, _warden = make_warden_deps(clock, **overrides)
    scope = _scope(deps)
    routed = route_spawn(scope)
    assert routed is not None and routed.decision.forage_shortfall is not None

    await record_routing(scope, routed, new_worker_id(clock))
    await _pump_one_request(queen_end)

    [request] = queen_end.forage_requests
    assert request.kind is ForageRequestKind.SHARED_SEATS
    assert request.wanted.source_id == "src_small"
    assert request.wanted.seats == 1
    assert request.task_id == scope.assignment.task_id
    assert request.grant_id == scope.grant_id
    assert request.tempo == scope.assignment.tempo
    assert "WORKER" in request.reason and "src_small" in request.reason


async def _pump_one_request(queen_end: QueenEnd) -> None:
    """Read the Queen's end until the Warden's ForageRequest has arrived."""
    await queen_end.pump_until(lambda: bool(queen_end.forage_requests))


def test_route_rebind_raises_the_floor_by_the_attempt_and_leaves_the_failed_binding() -> None:
    clock = FakeClock()
    # Grades 2, 3, 5: the slot's floor is 2, so the first routing keeps the primary.
    sources = [
        make_chain_source("small", grade=2),
        make_chain_source("mid", grade=3),
        make_chain_source("strong", grade=5),
    ]
    overrides = routing_overrides(clock, sources, keys=("small", "mid", "strong"))
    deps, _queen, _warden = make_warden_deps(clock, **overrides)

    first = route_rebind(_scope(deps, attempt=1), "worker")
    second = route_rebind(_scope(deps, attempt=2), "worker")

    assert first is not None and first.key == "mid"  # Floor 2 + 1 = 3 passes over the primary.
    assert second is not None and second.key == "strong"  # Floor 2 + 2 = 4 passes over both.
    assert second.decision.reason.floor == 4


def test_route_rebind_returns_nothing_when_the_failed_binding_is_still_the_best() -> None:
    clock = FakeClock()
    sources = [make_chain_source("small", grade=3), make_chain_source("strong", grade=3)]
    deps, _queen, _warden = make_warden_deps(clock, **routing_overrides(clock, sources))

    # Every grade is 3, the map's top: the raised floor is capped there and the primary clears it.
    assert route_rebind(_scope(deps, attempt=2), "worker") is None


def test_route_rebind_builds_on_the_operators_standing_floor() -> None:
    clock = FakeClock()
    sources = [
        make_chain_source("small", grade=3),
        make_chain_source("mid", grade=4),
        make_chain_source("strong", grade=5),
    ]
    rules = RoutingRules(floors={ModelSlot.WORKER: 4})
    overrides = routing_overrides(clock, sources, keys=("small", "mid", "strong"), rules=rules)
    deps, _queen, _warden = make_warden_deps(clock, **overrides)

    # The first routing already needed grade 4 and chose `mid`; one attempt later it needs 5.
    routed = route_rebind(_scope(deps, attempt=1), "mid")

    assert routed is not None and routed.key == "strong"
