"""Tests for the Queen's intake refusing a Night Veil goal that asks where its Cell is (10.3d).

A goal request that named NIGHT_VEIL is refused, with its reason on the row and each ask a
`guard.denied` on the trail, when its ceiling asks for a location family (before the planner is
ever called) or when a planned task needs a cloud metadata endpoint (before anything is
persisted). The same asks from a goal at any other tier are planned as usual.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/intake.py and src/hivemind/queen/goal_submission.py
    (codingrules section 5.1: split by feature from test_intake.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.planner.location for the asks and the typed refusal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from builders.human import make_goal_request, queen_responder, single_task_plan
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskFilter
from hivemind.cell import CombShieldLevel
from hivemind.llm import FakeLLMProvider
from hivemind.llm.models import LLMRequest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.intake import GoalRequest, GoalRequestState, receive
from hivemind.queen.ticks.intake import drain_goal_requests

_REPLY: dict[str, object] = {"action": "RECORD", "reason": "Nothing to say."}
_LOCATION_RULE = "guard.tier_floor.night_veil_location"


def _metadata_plan(goal: str) -> dict[str, object]:
    """A one-task plan whose task needs to reach the cloud metadata endpoint."""
    plan = single_task_plan(goal)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["needs"] = {"network_scopes": ["169.254.169.254"]}
    return plan


def _queen(
    build_plan: Callable[[str], dict[str, object]] = single_task_plan,
) -> tuple[QueenDeps, WardenLink, list[LLMRequest]]:
    """A Queen whose planner answers `build_plan`, recording every call it answers."""
    seen: list[LLMRequest] = []
    provider = FakeLLMProvider(responder=queen_responder(_REPLY, seen, build_plan))
    deps, link, _end = make_queen_deps(fake_provider=provider)
    return deps, link, seen


async def _settle(deps: QueenDeps, link: WardenLink, request: GoalRequest) -> GoalRequest:
    """Drain until the request's plan (if one started) settles; return the row as it stands."""
    await drain_goal_requests(deps, (link,))
    if deps.planning.task is not None:
        await asyncio.wait({deps.planning.task})
    await drain_goal_requests(deps, (link,))
    return await deps.goal_requests.get(request.id)


async def _denials(deps: QueenDeps) -> list[PheromoneEvent]:
    return list(await deps.trail.query(TrailQuery(kind="guard.denied")))


async def test_a_night_veil_ceiling_that_asks_for_location_is_refused_before_planning() -> None:
    deps, link, seen = _queen()
    request = make_goal_request(
        deps.clock, comb_shield=CombShieldLevel.NIGHT_VEIL, capabilities=("geo:*", "tool:*")
    )
    await receive(deps, request)

    settled = await _settle(deps, link, request)

    assert settled.state is GoalRequestState.REFUSED
    assert settled.refusal is not None and "geo:*" in settled.refusal
    assert seen == []  # Refused before the planner's model was ever called.
    [denial] = await _denials(deps)
    assert denial.payload["rule"] == _LOCATION_RULE
    assert denial.payload["capability"] == "geo:*"
    [refused] = await deps.trail.query(TrailQuery(kind="queen.goal_request_refused"))
    assert refused.payload["reason_code"] == "NightVeilLocationError"


async def test_a_night_veil_plan_needing_a_metadata_endpoint_is_refused_unpersisted() -> None:
    deps, link, seen = _queen(_metadata_plan)
    request = make_goal_request(deps.clock, comb_shield=CombShieldLevel.NIGHT_VEIL)
    await receive(deps, request)

    settled = await _settle(deps, link, request)

    assert settled.state is GoalRequestState.REFUSED
    assert len(seen) == 1  # Planned once, then refused before the graph was written.
    assert not await deps.chamber.list(TaskFilter())
    [denial] = await _denials(deps)
    assert denial.payload["capability"] == "net:169.254.169.254"
    assert denial.payload["rule"] == _LOCATION_RULE


async def test_the_same_asks_at_another_tier_are_planned_as_usual() -> None:
    deps, link, _seen = _queen(_metadata_plan)
    request = make_goal_request(deps.clock, capabilities=("geo:*", "net:*", "tool:*"))
    await receive(deps, request)

    settled = await _settle(deps, link, request)

    assert settled.state is GoalRequestState.PLANNED
    assert await _denials(deps) == []
