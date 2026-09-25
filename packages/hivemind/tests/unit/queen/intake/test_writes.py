"""Tests for hivemind.queen.intake.writes: every edge written with its own event, never the words.

Fits into the Hive:
    Mirrors src/hivemind/queen/intake/writes.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake.writes for the functions under test.
"""

from __future__ import annotations

import json

import pytest
from builders.human import make_goal_request
from builders.queen import make_queen_deps

from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.intake import (
    DECLINED_CODE,
    GoalRequest,
    GoalRequestNotFoundError,
    GoalRequestState,
    InvalidGoalRequestTransitionError,
    Refusal,
    confirm,
    decline,
    hold,
    mark_finished,
    mark_planned,
    receive,
    refuse,
    start_planning,
)
from waggle.ids import new_device_id, new_task_id

_WORDS = "Buy the red bicycle for my niece Alice."  # Personal words the trail must never hold.


async def _kinds(deps: QueenDeps) -> list[str]:
    return [event.kind for event in await deps.trail.query(TrailQuery())]


async def _received(deps: QueenDeps, **overrides: object) -> GoalRequest:
    return await receive(deps, make_goal_request(deps.clock, text=_WORDS, **overrides))


async def test_receive_commits_the_row_with_an_event_that_names_but_never_quotes_it() -> None:
    deps, _link, _end = make_queen_deps()
    device = new_device_id(deps.clock)

    request = await _received(deps, device_id=device)

    assert await deps.goal_requests.get(request.id) == request
    [event] = await deps.trail.query(TrailQuery())
    assert event.kind == "queen.goal_request_received"
    assert event.payload["goal_request_id"] == request.id
    assert event.payload["device_id"] == device
    assert _WORDS not in json.dumps(event.payload)


async def test_receive_refuses_a_request_that_is_not_fresh() -> None:
    deps, _link, _end = make_queen_deps()
    request = make_goal_request(deps.clock, needs_confirmation=True, confirmed_at=deps.clock.now())

    with pytest.raises(InvalidGoalRequestTransitionError):
        await receive(deps, request)

    assert await _kinds(deps) == []


async def test_planning_then_planned_writes_one_event_per_edge() -> None:
    deps, _link, _end = make_queen_deps()
    request = await _received(deps)
    goal_id = new_task_id(deps.clock)

    planned = await mark_planned(deps, await start_planning(deps, request), goal_id)

    assert planned.state is GoalRequestState.PLANNED
    assert planned.goal_id == goal_id
    assert await _kinds(deps) == [
        "queen.goal_request_received",
        "queen.goal_request_planning",
        "queen.goal_request_planned",
    ]


async def test_hold_then_confirm_returns_the_request_to_received_with_its_confirmation() -> None:
    deps, _link, _end = make_queen_deps()
    request = await _received(deps, needs_confirmation=True)
    await hold(deps, request)

    confirmed = await confirm(deps, request.id)

    assert confirmed.state is GoalRequestState.RECEIVED
    assert confirmed.confirmed_at is not None
    assert (await _kinds(deps))[-2:] == ["queen.goal_request_held", "queen.goal_request_confirmed"]


async def test_confirm_refuses_a_request_that_is_not_held() -> None:
    deps, _link, _end = make_queen_deps()
    request = await _received(deps)

    with pytest.raises(InvalidGoalRequestTransitionError):
        await confirm(deps, request.id)


async def test_confirm_of_an_unknown_request_raises() -> None:
    deps, _link, _end = make_queen_deps()

    with pytest.raises(GoalRequestNotFoundError):
        await confirm(deps, make_goal_request(deps.clock).id)


async def test_decline_refuses_a_held_request_and_keeps_the_reason_off_the_trail() -> None:
    deps, _link, _end = make_queen_deps()
    request = await _received(deps, needs_confirmation=True)
    await hold(deps, request)

    declined = await decline(deps, request.id, "That is not what I said about Alice.")

    assert declined.state is GoalRequestState.REFUSED
    assert declined.refusal == "That is not what I said about Alice."
    [event] = await deps.trail.query(TrailQuery(kind="queen.goal_request_refused"))
    assert event.payload["reason_code"] == DECLINED_CODE
    assert "Alice" not in json.dumps(event.payload)


async def test_decline_refuses_a_request_already_being_planned() -> None:
    deps, _link, _end = make_queen_deps()
    request = await _received(deps)
    await start_planning(deps, request)

    with pytest.raises(InvalidGoalRequestTransitionError):
        await decline(deps, request.id, "No.")


async def test_refuse_cuts_the_reason_to_the_rows_bound_and_records_only_its_code() -> None:
    deps, _link, _end = make_queen_deps()
    planning = await start_planning(deps, await _received(deps))

    refused = await refuse(deps, planning, Refusal(reason="x" * 5_000, code="PlannerError"))

    assert refused.refusal is not None
    assert len(refused.refusal) == 2_000
    [event] = await deps.trail.query(TrailQuery(kind="queen.goal_request_refused"))
    assert event.payload["reason_code"] == "PlannerError"


async def test_a_terminal_request_cannot_move_again() -> None:
    deps, _link, _end = make_queen_deps()
    planning = await start_planning(deps, await _received(deps))
    planned = await mark_planned(deps, planning, new_task_id(deps.clock))

    with pytest.raises(InvalidGoalRequestTransitionError):
        await start_planning(deps, planned)


async def test_mark_finished_stamps_a_planned_request_once_with_its_own_event() -> None:
    deps, _link, _end = make_queen_deps()
    planning = await start_planning(deps, await _received(deps))
    goal_id = new_task_id(deps.clock)
    planned = await mark_planned(deps, planning, goal_id)

    finished = await mark_finished(deps, planned)

    assert finished.finished_at is not None
    [event] = await deps.trail.query(TrailQuery(kind="queen.goal_request_finished"))
    assert event.payload == {"goal_request_id": planned.id, "goal_id": goal_id}
