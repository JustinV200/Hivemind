"""Define the Night Veil facts and checks the dispatcher adds before a Cell is chosen or armed.

Roadmap steps 10.3a and 10.3c (ADR-0030, ADR-0031): Night Veil work starts only from a human's
own durable goal request naming the tier, and runs only on a fresh Virtual Cell whose control
link is the Hive Stand's hidden service reached through Tor. The Guard's floors judge both from
the request's context, so `tier_context` adds to `hivemind.queen.authority.task_context` the two
facts only the Queen can look up: the goal request the task cites (who asked, and for which
tier; `goal_request_facts`) and the control link this Hive would hand a Night Veil Cell, from its
`[security.tiers.NIGHT_VEIL]` profile (`control_link`). `check_night_veil_placement` passes the
placement point's floors for a task that asks for or is bound to NIGHT_VEIL before `decide` ever
runs, acting as the Queen for the goal: a refusal is `guard.denied` on the trail and a final
`PlacementError`, since no later tick could make a planner's tier a human's, or give the Hive a
hidden service it was not configured with.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready._dispatch_one` (the placement
    check) and `hivemind.queen.dispatcher.acquire` (the egress point's context). Calls into
    `hivemind.brood_chamber` (Task), `hivemind.cell`, `hivemind.guard`, `hivemind.queen.
    authority`, `hivemind.queen.deps`, `hivemind.queen.intake` and `hivemind.queen.placement`
    only.

Key invariants:
    - Facts are added only when the action is under Night Veil (the Cell's tier or the task's
      bound or requested tier), so every other placement makes no store read at all.
    - A task that cites no goal request, or one whose row is gone, gets no request facts: the
      initiation floor then refuses it, never assumes a human asked.
    - The control link is read from configuration only; an unconfigured profile states none, and
      `hivemind.queen.placement.policy.check_night_veil` refuses that loudly instead.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the tier's boundary.
    - hivemind.guard.policy.floors.initiation and .night_veil for the floors that decide.
    - hivemind.hive.backends.bootstrap.cell_endpoint for where the link is actually handed over.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from hivemind.brood_chamber import Task
from hivemind.cell import CombShieldLevel
from hivemind.guard import CapabilitySet, EnforcementPoint, PolicyContext
from hivemind.guard.policy import ControlLink, GoalRequestFacts
from hivemind.guard.policy.floors import is_night_veil
from hivemind.queen.authority import goal_held, request_for, task_context
from hivemind.queen.deps import QueenDeps
from hivemind.queen.intake.errors import GoalRequestNotFoundError
from hivemind.queen.placement import PlacementError, PlacementPolicy, rules

_SCHEME_SEPARATOR = "://"  # A hidden-service address may be written as a whole URL.

__all__ = [
    "check_night_veil_placement",
    "control_link",
    "goal_request_facts",
    "tier_context",
]


async def goal_request_facts(deps: QueenDeps, task: Task) -> GoalRequestFacts | None:
    """Return who asked for `task`'s goal and for which tier, from the request it cites.

    Args:
        deps: The Queen's collaborators; `goal_requests` is read.
        task: Any task; `spec.goal_request_id` names its durable request, when it has one.

    Returns:
        The request's origin and requested tier, or None when the task cites no request or the
        row no longer exists.
    """
    request_id = task.spec.goal_request_id
    if request_id is None:
        return None  # A `hive run` or drafted graph: no durable human request stands behind it.
    try:
        request = await deps.goal_requests.get(request_id)
    except GoalRequestNotFoundError:
        return None  # Nothing a floor could cite; the initiation floor refuses on its absence.
    return GoalRequestFacts(origin=request.origin, comb_shield=request.comb_shield)


def control_link(policy: PlacementPolicy) -> ControlLink | None:
    """Return the control link a Night Veil Cell would be handed, as this Hive configured it.

    Args:
        policy: The placement policy; `night_veil` carries the Night Veil tier profile.

    Returns:
        The hidden-service host and the Tor SOCKS proxy (None when unset), or None when no
        profile or no hidden-service address is configured at all.
    """
    profile = policy.night_veil
    if profile is None or not profile.hive_stand_onion_address:
        return None  # check_night_veil names the missing profile or address in its refusal.
    address = profile.hive_stand_onion_address
    # A bare `host` or `host:port` parses as a netloc only behind `//`; a whole URL as itself.
    parts = urlsplit(address if _SCHEME_SEPARATOR in address else f"//{address}")
    host = parts.hostname or address
    return ControlLink(host=host, socks_proxy_url=profile.socks_proxy_url or None)


async def tier_context(
    deps: QueenDeps, task: Task, cell_tier: CombShieldLevel | None = None
) -> PolicyContext:
    """Return `task`'s context, with the Night Veil facts the floors read when they apply.

    Args:
        deps: The Queen's collaborators.
        task: The task the action is for.
        cell_tier: The tier of the Cell the action arms, when one is being provisioned or
            resumed for it (the egress point); None before any Cell is chosen.

    Returns:
        `task_context(task)`, with `comb_shield` set to `cell_tier` when given, plus the goal
        request facts and the configured control link when the action is under Night Veil.
    """
    context = task_context(task)
    if cell_tier is not None:
        context = context.model_copy(update={"comb_shield": cell_tier})
    if not is_night_veil(context):
        return context  # No Night Veil floor reads these facts, so none are looked up.
    facts = await goal_request_facts(deps, task)
    link = control_link(deps.placement_policy)
    return context.model_copy(update={"goal_request": facts, "control_link": link})


async def check_night_veil_placement(deps: QueenDeps, task: Task) -> None:
    """Pass the placement point's floors for a Night Veil task, before any Cell is chosen.

    Args:
        deps: The Queen's collaborators; `enforcer` decides and records a refusal.
        task: A ready task; nothing is checked unless it asks for or is bound to NIGHT_VEIL.

    Raises:
        PlacementError: A floor refused (a tier no human's request named, a control link that
            is not a hidden service through Tor); final, and `guard.denied` is on the trail.
    """
    context = await tier_context(deps, task)
    if not is_night_veil(context):
        return  # Every other tier: placement's own rules and the goal ceiling decide.
    # The Queen acts for the goal; the floors never read the held set, so an unset one is empty.
    held = goal_held(task) or CapabilitySet.empty()
    needed = rules.tier_capability(CombShieldLevel.NIGHT_VEIL)
    request = request_for(deps, EnforcementPoint.PLACEMENT, needed, held)
    decision = await deps.enforcer.check_floors(request.model_copy(update={"context": context}))
    if decision is not None:
        raise PlacementError(f"NIGHT_VEIL placement refused: {decision.reason}", final=True)
