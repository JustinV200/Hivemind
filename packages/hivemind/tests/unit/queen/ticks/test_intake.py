"""Tests for hivemind.queen.ticks.intake: every goal request planned exactly once, crash or not.

A "crash" here is a fresh Queen runtime (a new `PlanningLane` and wake signal) over the very same
stores: everything durable survives, nothing in memory does, which is exactly what a restart
after the Hive Entrance's `202` leaves behind. The planner's calls are counted, so "planned
exactly once" is asserted, not assumed.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/intake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.intake for the drain under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable

from builders.human import (
    RecordingHumanChannel,
    is_planning,
    make_goal_request,
    queen_responder,
    single_task_plan,
)
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import CombShieldLevel, Isolation, RequestOrigin
from hivemind.llm import FakeLLMProvider
from hivemind.llm.models import LLMRequest
from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import ChatKind, ChatQuery
from hivemind.queen.deps import PlanningLane, QueenDeps, WardenLink
from hivemind.queen.goal_submission import GoalTerms, plan_goal_graph
from hivemind.queen.intake import (
    GoalRequest,
    GoalRequestState,
    confirm,
    hold,
    receive,
    start_planning,
)
from hivemind.queen.ticks.intake import drain_goal_requests, stop_planning

_REPLY: dict[str, object] = {"action": "RECORD", "reason": "Nothing to say."}


@dataclasses.dataclass
class _Rig:
    """One Queen's collaborators, her attached Warden and what her planner was asked."""

    deps: QueenDeps
    link: WardenLink
    channel: RecordingHumanChannel
    seen: list[LLMRequest]

    def plans(self) -> int:
        """How many planning calls the planner answered so far."""
        return sum(1 for request in self.seen if is_planning(request))


def _rig(build_plan: Callable[[str], dict[str, object]] = single_task_plan) -> _Rig:
    seen: list[LLMRequest] = []
    responder = queen_responder(_REPLY, seen, build_plan)
    channel = RecordingHumanChannel()
    deps, link, _end = make_queen_deps(
        fake_provider=FakeLLMProvider(responder=responder), human_channel=channel
    )
    return _Rig(deps, link, channel, seen)


def _restart(rig: _Rig) -> _Rig:
    """The same durable stores under a fresh runtime: what a crash leaves behind."""
    deps = dataclasses.replace(rig.deps, planning=PlanningLane(), wake=asyncio.Event())
    return dataclasses.replace(rig, deps=deps)


async def _drain(rig: _Rig) -> None:
    """One drain, then let any plan it started finish, then one more drain to settle it."""
    await drain_goal_requests(rig.deps, (rig.link,))
    task = rig.deps.planning.task
    if task is not None:
        await asyncio.wait({task})
    await drain_goal_requests(rig.deps, (rig.link,))


async def _state(rig: _Rig, request: GoalRequest) -> GoalRequest:
    return await rig.deps.goal_requests.get(request.id)


async def test_a_received_request_is_planned_once_and_its_device_told() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))

    await _drain(rig)
    await _drain(rig)

    planned = await _state(rig, request)
    assert planned.state is GoalRequestState.PLANNED
    assert planned.goal_id is not None
    assert rig.plans() == 1
    assert rig.channel.names() == ["goal_request_planned"]
    root = await rig.deps.chamber.get(planned.goal_id)
    assert root.spec.goal_request_id == request.id


async def test_the_request_terms_reach_every_task_of_its_goal() -> None:
    rig = _rig()
    request = await receive(
        rig.deps,
        make_goal_request(
            rig.deps.clock,
            comb_shield=CombShieldLevel.NIGHT_VEIL,
            budget_usd=1.25,
            capabilities=("cell:virtual", "llm:*"),
        ),
    )

    await _drain(rig)

    goal_id = (await _state(rig, request)).goal_id
    [task] = await rig.deps.chamber.list(TaskFilter(goal_id=goal_id))
    # ADR-0031's floor reads exactly these: a human origin, the requested tier and the request.
    assert task.spec.origin is RequestOrigin.HUMAN
    assert task.spec.needs.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert task.spec.needs.isolation is Isolation.REQUIRED
    assert task.spec.goal_request_id == request.id
    assert task.spec.spend_cap_usd == 1.25
    assert task.spec.capabilities == ("cell:virtual", "llm:*")


async def test_a_crash_while_received_loses_nothing() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))

    restarted = _restart(rig)
    await _drain(restarted)

    assert (await _state(restarted, request)).state is GoalRequestState.PLANNED
    assert restarted.plans() == 1


async def test_a_crash_after_the_graph_was_persisted_marks_it_planned_without_replanning() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))
    planning = await start_planning(rig.deps, request)
    # The crash lands after the chamber persisted the graph, before the row was marked PLANNED.
    terms = GoalTerms(clearance=request.clearance, goal_request_id=request.id)
    goal_id = await plan_goal_graph(rig.deps, (rig.link,), request.text, terms)
    assert (await _state(rig, planning)).state is GoalRequestState.PLANNING

    restarted = _restart(rig)
    await _drain(restarted)

    settled = await _state(restarted, request)
    assert settled.state is GoalRequestState.PLANNED
    assert settled.goal_id == goal_id
    assert restarted.plans() == 1  # The one plan before the crash; never a second.
    assert len(await restarted.deps.chamber.list(TaskFilter())) == 1


async def test_a_crash_mid_plan_plans_it_again_exactly_once() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))
    await start_planning(rig.deps, request)  # The crash lands before the planner returned.

    restarted = _restart(rig)
    await _drain(restarted)
    await _drain(restarted)

    assert (await _state(restarted, request)).state is GoalRequestState.PLANNED
    assert restarted.plans() == 1
    assert len(await restarted.deps.chamber.list(TaskFilter())) == 1


async def test_a_plan_in_flight_is_never_started_a_second_time() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))
    await start_planning(rig.deps, request)
    never = asyncio.Event()

    async def never_finishes() -> None:
        await never.wait()

    # White-box: a plan for this row is in flight beside the tick, one that never finishes.
    in_flight = asyncio.ensure_future(never_finishes())
    rig.deps.planning.request_id, rig.deps.planning.task = request.id, in_flight

    await drain_goal_requests(rig.deps, (rig.link,))

    assert rig.plans() == 0
    assert (await _state(rig, request)).state is GoalRequestState.PLANNING
    await stop_planning(rig.deps.planning)  # What Queen.stop does: cancelled, reaped, lane empty.
    assert in_flight.cancelled()
    assert rig.deps.planning.task is None


async def test_settled_requests_are_left_alone_after_a_crash() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))
    await _drain(rig)
    events_before = len(await rig.deps.trail.query(TrailQuery()))

    restarted = _restart(rig)
    await _drain(restarted)

    assert restarted.plans() == 1
    assert (await _state(restarted, request)).state is GoalRequestState.PLANNED
    assert len(await restarted.deps.trail.query(TrailQuery())) == events_before


async def test_a_request_that_needs_confirming_is_held_echoed_and_waits_through_a_crash() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock, needs_confirmation=True))

    await _drain(rig)
    restarted = _restart(rig)
    await _drain(restarted)

    assert (await _state(restarted, request)).state is GoalRequestState.AWAITING_CONFIRMATION
    assert restarted.plans() == 0
    [echo] = await rig.deps.chat.read(ChatQuery())
    assert (echo.kind, echo.ref) == (ChatKind.NOTICE, request.id)
    assert request.text in echo.text
    assert rig.channel.names() == ["goal_request_held"]


async def test_a_confirmed_request_is_planned_once() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock, needs_confirmation=True))
    await hold(rig.deps, request)

    await confirm(rig.deps, request.id)
    await _drain(rig)

    confirmed = await _state(rig, request)
    assert confirmed.state is GoalRequestState.PLANNED
    assert confirmed.confirmed_at is not None
    assert rig.plans() == 1


async def test_a_plan_the_planner_cannot_produce_refuses_the_request_and_says_why() -> None:
    def unusable_plan(goal: str) -> dict[str, object]:
        return {"tasks": [{"key": "a", "depends_on": ["a"]}]}  # Not even a valid plan shape.

    rig = _rig(unusable_plan)
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))

    await _drain(rig)

    refused = await _state(rig, request)
    assert refused.state is GoalRequestState.REFUSED
    assert refused.refusal
    [event] = await rig.deps.trail.query(TrailQuery(kind="queen.goal_request_refused"))
    assert event.payload["reason_code"] in {"MalformedOutputError", "PlannerError"}
    assert request.text not in json.dumps(event.payload)
    [notice] = await rig.deps.chat.read(ChatQuery())
    assert notice.kind is ChatKind.NOTICE
    assert rig.channel.names() == ["goal_request_refused"]


async def test_nothing_is_planned_while_the_queens_own_model_is_clustered() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))
    rig.deps.cluster_state.mark_clustered("fake")

    await _drain(rig)

    assert (await _state(rig, request)).state is GoalRequestState.RECEIVED
    assert rig.plans() == 0


async def test_a_finished_goal_is_announced_once() -> None:
    rig = _rig()
    request = await receive(rig.deps, make_goal_request(rig.deps.clock))
    await _drain(rig)
    goal_id = (await _state(rig, request)).goal_id
    assert goal_id is not None
    await rig.deps.chamber.cancel(goal_id, "The human changed their mind.")

    await _drain(rig)
    await _drain(rig)

    finished = await _state(rig, request)
    assert finished.finished_at is not None
    assert rig.channel.names() == ["goal_request_planned", "goal_finished"]
    [task] = await rig.deps.chamber.list(TaskFilter(goal_id=goal_id))
    assert task.status is TaskStatus.CANCELLED
