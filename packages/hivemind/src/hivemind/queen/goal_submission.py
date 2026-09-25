"""Define submit_goal and plan_goal_graph: plan a goal into a task graph, persist it, place it.

`Queen.submit_goal` used to do this work in its own class body; it is pulled out to module-level
functions, mirroring `hivemind.queen.dispatcher.dispatch_ready`, so `queen.py` (pinned at
codingrules 5.1's file cap) never grows to carry a new `PlanBrief` field (roadmap step 5.0b:
`scratch_root`, so a plan that declares a leaving inside the Hive Stand's own scratch is refused
while planning; roadmap step 5.0e: `keep_root`; roadmap step 10.3: the submitter's capability
set, every planned task's ceiling). Roadmap step 10.5 (ADR-0040) splits the work in two and
groups what a goal is planned under into `GoalTerms`: `plan_goal_graph` plans and persists (the
half the Queen's own intake drain runs for a durable goal request, beside her tick, with the
request's budget, tier, origin, device, ceiling and id), and `submit_goal` adds the immediate
dispatch `hive run` relies on, unchanged for it: it passes no request (`hive tasks submit` writes
a drafted graph to the Brood Chamber itself and never plans). Roadmap step 10.3d: a goal whose
request named NIGHT_VEIL is refused before it is planned when its ceiling asks where its Cell is,
and before its graph is persisted when a planned task's needs do
(`hivemind.queen.planner.location`); each ask is refused through the Guard's Night Veil floor at
the placement point, the Queen acting for the goal, so it is `guard.denied` on the trail. A Night
Veil goal that is planned has every task expected in the Night Veil boundary's segments (the
ones behind her trail, `hivemind.pheromone.segments_of`) the moment it is persisted, so the
Queen's own records about those tasks, its `queen.planned` among them, keep only their skeleton
(codingrules section 12); the Brood Chamber cuts the tasks' own transitions itself.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen.Queen.submit_goal` (`submit_goal`) and `hivemind.queen.ticks.intake`
    (`plan_goal_graph`). Calls into `hivemind.brood_chamber.task` (GoalRequestId), `hivemind.cell`,
    `hivemind.forage.slots` (ModelSlot), `hivemind.guard` (the location asks' refusals),
    `hivemind.queen.authority`, `hivemind.queen.deps`, `hivemind.queen.dispatcher`
    (dispatch_ready), `hivemind.queen.planner` (PlanBrief, plan_goal, location),
    `hivemind.pheromone` (segments_of), `hivemind.queen.trail` (record_event) and waggle only.

Key invariants:
    - Takes `deps` and `wardens` explicitly, never a `Queen` instance: the Queen delegates by
      handing her own collaborators to a free function (codingrules section 8.8).
    - The returned TaskId is always the first task minted from the plan (the goal's own id).
    - Every task minted from one goal carries the same `GoalTerms` facts the caller passed; None
      (the operator's own local path) stays None, never an empty set or a zero budget.

See Also:
    - .claude/codingrules.md section 5.1 for the file-size limit this module exists to keep.
    - hivemind.queen.planner.plan for plan_goal and PlanBrief, this module's one model call.
    - hivemind.queen.intake for the goal requests `plan_goal_graph` plans.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.brood_chamber import Task
from hivemind.brood_chamber.task import GoalRequestId
from hivemind.cell import CombShieldLevel, HoneyClearance, RequestOrigin
from hivemind.forage.slots import ModelSlot
from hivemind.guard import Capability, CapabilitySet, EnforcementPoint, PolicyContext
from hivemind.guard.policy import GoalRequestFacts
from hivemind.pheromone import segments_of
from hivemind.queen.authority import request_for
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.planner import PlanBrief, plan_goal
from hivemind.queen.planner.location import (
    NightVeilLocationError,
    ceiling_location_asks,
    needs_location_asks,
)
from hivemind.queen.trail import record_event
from waggle.ids import DeviceId, TaskId

__all__ = ["GoalTerms", "plan_goal_graph", "submit_goal"]


@dataclass(frozen=True, slots=True)
class GoalTerms:
    """What a goal is planned under, beyond its words (codingrules 5.1's argument group).

    Attributes:
        clearance: The goal's data-sensitivity ceiling; every planned task is at or below it.
        capabilities: The submitter's capability set, every task's ceiling (roadmap step 10.3);
            None for the operator's own local path, which has no device ceiling.
        origin: Who asked for the goal; every planned task inherits it (roadmap step 5.7a).
        comb_shield: The tier the human's request named; every planned task needs it. None
            leaves each task's tier to the planner.
        spend_cap_usd: The request's budget, the goal's own spend cap; None for none of its own.
        device_id: The enrolled device that asked; recorded with the plan, never on a task.
        goal_request_id: The durable request this goal is planned from; every task carries it,
            which is how a crash between persisting and marking the request PLANNED is found.
    """

    clearance: HoneyClearance
    capabilities: tuple[str, ...] | None = None
    origin: RequestOrigin = RequestOrigin.HUMAN
    comb_shield: CombShieldLevel | None = None
    spend_cap_usd: float | None = None
    device_id: DeviceId | None = None
    goal_request_id: GoalRequestId | None = None


async def submit_goal(
    deps: QueenDeps, wardens: Sequence[WardenLink], goal: str, terms: GoalTerms
) -> TaskId:
    """Plan `goal` into a task graph, persist it, and place whatever is ready at once.

    Args:
        deps: The Queen's own collaborators (`Queen._deps`).
        wardens: Every Warden currently attached (`Queen.wardens`); placement matches the plan's
            needs against their Cells, and `dispatch_ready` assigns to them.
        goal: The goal text, as the human (or a bee on the human's behalf) stated it.
        terms: What the goal is planned under.

    Returns:
        The goal's own id (the first task minted from the plan).

    Raises:
        hivemind.queen.planner.PlannerError: The plan could not become a valid task graph; a
            string in `terms.capabilities` that is not a capability fails the same way.
    """
    goal_id = await plan_goal_graph(deps, wardens, goal, terms)
    await dispatch_ready(deps, wardens)
    return goal_id


async def plan_goal_graph(
    deps: QueenDeps, wardens: Sequence[WardenLink], goal: str, terms: GoalTerms
) -> TaskId:
    """Plan `goal` into a task graph and persist it, without dispatching anything.

    Args:
        deps: The Queen's own collaborators.
        wardens: Every Warden attached when planning began; their Cells describe the fleet.
        goal: The goal text, as the human stated it.
        terms: What the goal is planned under.

    Returns:
        The goal's own id (the first task minted from the plan).

    Raises:
        hivemind.queen.planner.PlannerError: The plan could not become a valid task graph.
        hivemind.llm.errors.LLMError: The planner's model call failed on every rung and binding.
        hivemind.queen.planner.location.NightVeilLocationError: A Night Veil goal's ceiling or
            planned needs ask where its Cell is (roadmap step 10.3d); nothing was persisted.
    """
    night_veil = terms.comb_shield is CombShieldLevel.NIGHT_VEIL
    if night_veil:
        # Before the model call: a ceiling that asks for location is refused without planning.
        await _refuse_location_asks(deps, terms, ceiling_location_asks(terms.capabilities))
    bound = deps.bound_for(ModelSlot.QUEEN)
    brief = PlanBrief(
        goal,
        terms.clearance,
        [link.cell for link in wardens],
        origin=terms.origin,
        scratch_root=deps.scratch_root,
        keep_root=deps.keep_root,
        capabilities=terms.capabilities,
        comb_shield=terms.comb_shield,
        goal_request_id=terms.goal_request_id,
        spend_cap_usd=terms.spend_cap_usd,
    )
    # External await: one model call, seconds to minutes on a local model; the ladder retries
    # and steps down rungs itself, and a failure on every rung raises for the caller to refuse.
    draft = await plan_goal(brief, bound, gate=deps.call_gate)
    if night_veil:
        # Before persisting: no task of a Night Veil goal may ask where its Cell is either.
        needs = (task.needs for task in draft.tasks)
        await _refuse_location_asks(deps, terms, needs_location_asks(needs))
    minted = await deps.chamber.submit(draft)
    _veil_night_veil_tasks(deps, terms, minted)
    await record_event(deps, "queen.planned", minted[0].id, **_planned_payload(len(minted), terms))
    return minted[0].id  # The goal's own id: the first task minted from the plan.


def _veil_night_veil_tasks(deps: QueenDeps, terms: GoalTerms, minted: Sequence[Task]) -> None:
    """Expect a Night Veil goal's tasks in the boundary's segments, before any Cell exists.

    Codingrules 12: from here on the Queen's own records about these tasks (the plan's own
    `queen.planned` first) reach the trail as the skeleton only; a Hive whose trail is not veiled
    has no Virtual side, so no Night Veil Cell could ever take them.
    """
    night_veil = segments_of(deps.trail)
    if night_veil is None or terms.comb_shield is not CombShieldLevel.NIGHT_VEIL:
        return
    for task in minted:
        night_veil.expect(task.id)


def _planned_payload(task_count: int, terms: GoalTerms) -> dict[str, JsonValue]:
    """Build `queen.planned`'s payload: the count, plus the request's ids when it came from one."""
    payload: dict[str, JsonValue] = {"task_count": task_count}
    # Only a requested goal names these; the operator's own path keeps today's exact payload.
    if terms.goal_request_id is not None:
        payload["goal_request_id"] = terms.goal_request_id
    if terms.device_id is not None:
        payload["device_id"] = terms.device_id
    return payload


async def _refuse_location_asks(
    deps: QueenDeps, terms: GoalTerms, asks: tuple[Capability, ...]
) -> None:
    """Refuse each location ask through the Guard, then the goal; nothing when there is none.

    The Queen acts for the goal at the placement point with the goal's own facts on the context
    (its bound tier, origin and request), so the Night Veil floor is what refuses each ask and
    records its `guard.denied`.

    Raises:
        NightVeilLocationError: `asks` is not empty.
    """
    if not asks:
        return
    facts = (
        GoalRequestFacts(origin=terms.origin, comb_shield=terms.comb_shield)
        if terms.goal_request_id is not None
        else None
    )
    context = PolicyContext(bound_tier=terms.comb_shield, origin=terms.origin, goal_request=facts)
    held = CapabilitySet.parse(*(terms.capabilities or ()))
    for ask in asks:
        request = request_for(deps, EnforcementPoint.PLACEMENT, ask, held)
        await deps.enforcer.check_floors(request.model_copy(update={"context": context}))
    raise NightVeilLocationError(asks)
