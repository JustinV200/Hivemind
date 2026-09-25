"""Tests for hivemind.queen.queen.Queen's goal requests: durable first, planned once, by her tick.

Roadmap step 10.5 (ADR-0040, "A goal is durable before it is acknowledged"): `request_goal`
commits a row and wakes the Queen, and she plans it herself on her own tick, with no Warden
traffic needed to start one. A request held for the human's yes waits until it is confirmed or
declined, and a Queen that died after the row was committed leaves a successor exactly one plan to
make. These run the real `Queen.run` loop; tests/unit/queen/ticks/test_intake.py covers each
crash state against the drain directly.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_chat.py and the rest. Exercises hivemind.queen.chat.door and
    hivemind.queen.ticks.human.intake together with the Queen's own tick.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake for the rows, their states and their writes.
"""

from __future__ import annotations

import asyncio
import dataclasses

from builders.human import (
    RecordingHumanChannel,
    is_planning,
    make_goal_request,
    queen_responder,
    wait_until,
)
from builders.queen import make_queen_deps

from hivemind.llm import FakeLLMProvider
from hivemind.llm.models import LLMRequest
from hivemind.queen.deps import PlanningLane, QueenDeps
from hivemind.queen.intake import GoalRequest, GoalRequestState
from hivemind.queen.queen import Queen

_DECISION: dict[str, object] = {"action": "RECORD", "reason": "Nothing to decide."}


def _queen(channel: RecordingHumanChannel, seen: list[LLMRequest]) -> tuple[Queen, QueenDeps]:
    """A Queen whose planner answers a one-task plan and records every call in `seen`."""
    provider = FakeLLMProvider(responder=queen_responder(_DECISION, seen))
    deps, _link, _end = make_queen_deps(fake_provider=provider, human_channel=channel)
    return Queen(deps), deps


def _plans(seen: list[LLMRequest]) -> int:
    """How many planning calls the planner answered."""
    return sum(1 for request in seen if is_planning(request))


async def _state_is(deps: QueenDeps, request: GoalRequest, state: GoalRequestState) -> bool:
    return (await deps.goal_requests.get(request.id)).state is state


async def test_request_goal_wakes_her_and_she_plans_it_exactly_once() -> None:
    seen: list[LLMRequest] = []
    channel = RecordingHumanChannel()
    queen, deps = _queen(channel, seen)
    run_task = asyncio.ensure_future(queen.run())

    request = make_goal_request(deps.clock)
    request_id = await queen.request_goal(request)
    # Committed before the call returned: this is what the Entrance's `202` stands on.
    assert (await deps.goal_requests.get(request_id)).state is not GoalRequestState.REFUSED
    await wait_until(lambda: _state_is(deps, request, GoalRequestState.PLANNED))
    # A few idle wakes afterwards must never plan it again.
    for _ in range(3):
        deps.wake.set()
        await asyncio.sleep(0)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    planned = await deps.goal_requests.get(request_id)
    assert planned.goal_id is not None
    assert (await deps.chamber.get(planned.goal_id)).spec.goal_request_id == request_id
    assert _plans(seen) == 1
    assert channel.names() == ["goal_request_planned"]


async def test_a_held_request_waits_for_the_humans_yes_then_is_planned() -> None:
    seen: list[LLMRequest] = []
    channel = RecordingHumanChannel()
    queen, deps = _queen(channel, seen)
    run_task = asyncio.ensure_future(queen.run())

    request = make_goal_request(deps.clock, needs_confirmation=True)
    await queen.request_goal(request)
    await wait_until(lambda: _state_is(deps, request, GoalRequestState.AWAITING_CONFIRMATION))
    assert _plans(seen) == 0  # Nothing spends a cent before the human says yes.
    confirmed = await queen.confirm_goal_request(request.id)
    await wait_until(lambda: _state_is(deps, request, GoalRequestState.PLANNED))
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert confirmed.confirmed_at is not None
    assert _plans(seen) == 1
    assert channel.names() == ["goal_request_held", "goal_request_planned"]


async def test_a_declined_request_is_refused_and_never_planned() -> None:
    seen: list[LLMRequest] = []
    channel = RecordingHumanChannel()
    queen, deps = _queen(channel, seen)
    run_task = asyncio.ensure_future(queen.run())

    request = make_goal_request(deps.clock, needs_confirmation=True)
    await queen.request_goal(request)
    await wait_until(lambda: _state_is(deps, request, GoalRequestState.AWAITING_CONFIRMATION))
    declined = await queen.decline_goal_request(request.id, "Misheard: I said a haiku, not a hike.")
    deps.wake.set()
    await asyncio.sleep(0)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert declined.state is GoalRequestState.REFUSED
    assert _plans(seen) == 0
    assert channel.names() == ["goal_request_held", "goal_request_refused"]


async def test_a_request_committed_by_a_queen_that_died_is_planned_once_by_the_next() -> None:
    seen: list[LLMRequest] = []
    first, deps = _queen(RecordingHumanChannel(), seen)
    # The first Queen commits the row and dies before any tick of hers ever ran.
    request = make_goal_request(deps.clock)
    await first.request_goal(request)
    channel = RecordingHumanChannel()
    restarted = dataclasses.replace(
        deps, planning=PlanningLane(), wake=asyncio.Event(), human_channel=channel
    )
    restarted.wake.set()  # What a fresh QueenDeps starts with: drain what a crash left.
    second = Queen(restarted)
    run_task = asyncio.ensure_future(second.run())

    await wait_until(lambda: _state_is(restarted, request, GoalRequestState.PLANNED))
    await second.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert _plans(seen) == 1
    assert channel.names() == ["goal_request_planned"]
