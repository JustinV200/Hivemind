"""Contract suite for GoalRequestStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.queen.intake.protocol.GoalRequestStore contract and runs against both implementations
    that ship: InMemoryGoalRequestStore (over MemoryPheromoneTrail) and SqliteGoalRequestStore
    (over SqlitePheromoneTrail, both on the same tmp_path SQLite file). A new implementation joins
    the fixture's params and must pass here before it is used anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake.protocol for the GoalRequestStore protocol under test.
    - packages/hivemind/tests/contracts/test_leavings_store_contract.py for the pattern mirrored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from builders.human import make_goal_request

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import (
    MemoryPheromoneTrail,
    PheromoneTrail,
    QueenEvent,
    SqlitePheromoneTrail,
    TrailQuery,
)
from hivemind.queen.intake import (
    GoalRequest,
    GoalRequestExistsError,
    GoalRequestNotFoundError,
    GoalRequestQuery,
    GoalRequestState,
    GoalRequestStore,
    InMemoryGoalRequestStore,
    SqliteGoalRequestStore,
)
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_event_id, new_hive_id, new_node_id, new_task_id

_STORE_KINDS = ("memory", "sqlite")


@dataclass(frozen=True, slots=True)
class _Fixture:
    """A GoalRequestStore, the trail it records on, and the clock both share."""

    store: GoalRequestStore
    trail: PheromoneTrail
    clock: FakeClock


@pytest.fixture(params=_STORE_KINDS)
async def fx(request: pytest.FixtureRequest, tmp_path: Path) -> _Fixture:
    """A store of the parametrised kind, over a trail and a clock of its own."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _Fixture(InMemoryGoalRequestStore(memory_trail), memory_trail, clock)
    # Two connections to one file (ADR-0006); the trail's migration runs before the store's own.
    db_path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db_path), clock)
    sqlite_store = await SqliteGoalRequestStore.create(connect(db_path), clock)
    return _Fixture(sqlite_store, sqlite_trail, clock)


def _event(clock: FakeClock, request: GoalRequest, kind: str) -> QueenEvent:
    """Build a well-formed goal request event naming `request`."""
    return QueenEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=new_hive_id(clock),
        payload={"goal_request_id": request.id},
    )


def _received(fx: _Fixture, request: GoalRequest) -> QueenEvent:
    """The event an insert records."""
    return _event(fx.clock, request, "queen.goal_request_received")


async def _kinds(trail: PheromoneTrail) -> list[str]:
    return [event.kind for event in await trail.query(TrailQuery())]


async def test_insert_then_get_returns_an_equal_request_and_records_its_event(fx: _Fixture) -> None:
    request = make_goal_request(fx.clock, budget_usd=2.5, capabilities=("llm:*",))

    await fx.store.insert(request, _received(fx, request))

    assert await fx.store.get(request.id) == request
    assert await _kinds(fx.trail) == ["queen.goal_request_received"]


async def test_insert_of_a_taken_id_raises_and_writes_nothing(fx: _Fixture) -> None:
    request = make_goal_request(fx.clock)
    await fx.store.insert(request, _received(fx, request))

    with pytest.raises(GoalRequestExistsError):
        await fx.store.insert(request, _received(fx, request))

    assert await _kinds(fx.trail) == ["queen.goal_request_received"]


async def test_update_replaces_the_row_and_records_its_event(fx: _Fixture) -> None:
    request = make_goal_request(fx.clock)
    await fx.store.insert(request, _received(fx, request))
    planning = request.model_copy(update={"state": GoalRequestState.PLANNING})
    # The trail orders by (at, node_id) and each event here mints its own node id, so two events
    # at one instant would sort by a random id; a second apart they sort by time, as in a Hive.
    fx.clock.advance(1)

    await fx.store.update(planning, _event(fx.clock, planning, "queen.goal_request_planning"))

    assert (await fx.store.get(request.id)).state is GoalRequestState.PLANNING
    assert await _kinds(fx.trail) == ["queen.goal_request_received", "queen.goal_request_planning"]


async def test_update_of_an_unknown_id_raises_and_records_nothing(fx: _Fixture) -> None:
    request = make_goal_request(fx.clock)

    with pytest.raises(GoalRequestNotFoundError):
        await fx.store.update(request, _event(fx.clock, request, "queen.goal_request_planning"))

    assert await _kinds(fx.trail) == []


async def test_get_of_an_unknown_id_raises(fx: _Fixture) -> None:
    with pytest.raises(GoalRequestNotFoundError):
        await fx.store.get(make_goal_request(fx.clock).id)


async def test_an_event_naming_another_request_is_refused_before_anything_is_written(
    fx: _Fixture,
) -> None:
    request, other = make_goal_request(fx.clock), make_goal_request(fx.clock)

    with pytest.raises(InvariantViolationError):
        await fx.store.insert(request, _received(fx, other))

    with pytest.raises(GoalRequestNotFoundError):
        await fx.store.get(request.id)


async def test_an_event_of_another_kind_is_refused(fx: _Fixture) -> None:
    request = make_goal_request(fx.clock)

    with pytest.raises(InvariantViolationError):
        await fx.store.insert(request, _event(fx.clock, request, "queen.replied"))


async def test_list_requests_filters_by_state_oldest_first(fx: _Fixture) -> None:
    first = make_goal_request(fx.clock)
    fx.clock.advance(1.0)
    second = make_goal_request(fx.clock)
    fx.clock.advance(1.0)
    held = make_goal_request(fx.clock, needs_confirmation=True)
    for request in (second, held, first):
        await fx.store.insert(request, _received(fx, request))
    await fx.store.update(
        held.model_copy(update={"state": GoalRequestState.AWAITING_CONFIRMATION}),
        _event(fx.clock, held, "queen.goal_request_held"),
    )

    received = await fx.store.list_requests(GoalRequestQuery(state=GoalRequestState.RECEIVED))

    assert [request.id for request in received] == [first.id, second.id]


async def test_list_requests_filters_by_device_and_respects_the_limit(fx: _Fixture) -> None:
    device = new_device_id(fx.clock)
    mine = [make_goal_request(fx.clock, device_id=device) for _ in range(3)]
    for request in (*mine, make_goal_request(fx.clock)):
        await fx.store.insert(request, _received(fx, request))

    page = await fx.store.list_requests(GoalRequestQuery(device_id=device, limit=2))

    assert len(page) == 2
    assert all(request.device_id == device for request in page)


async def test_list_requests_unfinished_leaves_out_a_finished_goal(fx: _Fixture) -> None:
    open_goal = _planned(make_goal_request(fx.clock), fx.clock)
    done_goal = _planned(make_goal_request(fx.clock), fx.clock)
    done_goal = done_goal.model_copy(update={"finished_at": fx.clock.now() + timedelta(seconds=1)})
    for request in (open_goal, done_goal):
        await fx.store.insert(request, _received(fx, request))

    unfinished = await fx.store.list_requests(
        GoalRequestQuery(state=GoalRequestState.PLANNED, unfinished=True)
    )

    assert [request.id for request in unfinished] == [open_goal.id]


def _planned(request: GoalRequest, clock: FakeClock) -> GoalRequest:
    """Return `request` as a PLANNED row, as if stored after planning (for filter tests only)."""
    return request.model_copy(
        update={"state": GoalRequestState.PLANNED, "goal_id": new_task_id(clock)}
    )
