"""Commit the goals devices give the Hive: weigh each against its device's day, then write it down.

A goal reaches the Hive Entrance (the Hive's one door) three ways: typed on ``POST /v1/goals``,
spoken on ``POST /v1/chat/audio`` or the chat socket (roadmap step 10.5f), or held for a person's
step-up and confirmed later. Every one becomes a durable ``GoalRequest`` in the Queen's own tables
before the Entrance answers (ADR-0032), and every one is weighed against ADR-0033's spend rules
first. This module is what those paths share, so the rules are written once: ``goal_spend`` weighs
a goal against the device's last day of goals (each counted at its budget or the manifest's
per-goal cap, failing closed when the page could hide more), ``goal_request`` builds the request
from its held payload (the id minted when it was first asked for, the words, the budget, tier,
clearance, and for a spoken goal its source and whether it must be confirmed), and
``submit_held_goal`` commits it exactly once under that id. The Hive Stand console is the
operator's own path, so its goals carry no device ceiling, as ``hive run``'s do.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Called by the goals
    route, the confirmation route, the start-up recovery and the voice door. Calls into the
    Queen's door and goal-request table and the step-up rules' ``GoalSpend``.

Key invariants:
    - A held goal is committed at most once, under the id minted when it was held.
    - A device's daily cap is never read short: a full page of recent goals counts as unlimited.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "A goal is durable before
      it is acknowledged".
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import timedelta

from pydantic import JsonValue

from hivemind.cell import RequestOrigin
from hivemind.entrance.auth.step_up import GoalSpend
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.gate.services import EntranceServices
from hivemind.queen import GoalRequest, GoalRequestQuery, GoalRequestState
from hivemind.queen.intake import MAX_REQUEST_PAGE, GoalRequestExistsError

SPEND_WINDOW = timedelta(days=1)  # A device's daily cap is weighed over its last day of goals.

__all__ = ["SPEND_WINDOW", "goal_request", "goal_spend", "submit_held_goal"]


async def goal_spend(
    services: EntranceServices, device: EnrolledDevice, budget_usd: float | None
) -> GoalSpend:
    """Weigh a goal against the device's last day of goals, each at its budget or the cap.

    Args:
        services: The Entrance's services (the goal-request table, the per-goal cap, the clock).
        device: The device asking.
        budget_usd: The new goal's own budget; None counts it at the manifest's per-goal cap.

    Returns:
        The goal's weight and the device's spend today, for ``requires_step_up``.
    """
    cap = services.rules.goal_spend_cap_usd
    since = services.clock.now() - SPEND_WINDOW
    query = GoalRequestQuery(device_id=device.id, received_since=since, limit=MAX_REQUEST_PAGE)
    # Latency: one local indexed read of the Queen's goal-request table.
    recent = await services.hive.goal_requests.list_requests(query)
    # A full page may hide more: fail closed, so the daily cap can never be read short.
    if len(recent) >= MAX_REQUEST_PAGE:
        return GoalSpend(budget_usd=_counted(budget_usd, cap), spent_today_usd=math.inf)
    spent = sum(
        _counted(request.budget_usd, cap)
        for request in recent
        if request.state is not GoalRequestState.REFUSED
    )
    return GoalSpend(budget_usd=_counted(budget_usd, cap), spent_today_usd=spent)


def goal_request(
    services: EntranceServices, device: EnrolledDevice, held: Mapping[str, JsonValue]
) -> GoalRequest:
    """Build the goal request a held payload describes, received now, from ``device``.

    Args:
        services: The Entrance's services (the clock).
        device: The device that asked for it, as it stands now (its set is the ceiling).
        held: The payload built when the goal was asked for: its minted id, its words, budget,
            tier and clearance, and optionally its source and whether it must be confirmed.

    Returns:
        The request, RECEIVED, validated.
    """
    now = services.clock.now()
    return GoalRequest.model_validate(
        {
            **held,
            "origin": RequestOrigin.HUMAN,
            "device_id": device.id,
            # The console is the operator at the Hive Stand: no device ceiling, as hive run.
            "capabilities": None if device.loopback_bound else tuple(device.capabilities),
            "received_at": now,
            "updated_at": now,
        }
    )


async def submit_held_goal(
    services: EntranceServices, device: EnrolledDevice, held: Mapping[str, JsonValue]
) -> str:
    """Commit a goal request from its held payload, exactly once under its minted id.

    Args:
        services: The Entrance's services.
        device: The device that submitted it, as it stands now (its set is the ceiling).
        held: The payload the goal was asked for with (see ``goal_request``).

    Returns:
        The goal request's id, once committed (at once when a retry finds it committed).
    """
    request = goal_request(services, device, held)
    try:
        # Latency: one local transaction in the Queen's tables, then her wake signal.
        return await services.queen.request_goal(request)
    except GoalRequestExistsError:
        # Already committed by an earlier attempt: the goal exists exactly once.
        return request.id


def _counted(budget_usd: float | None, cap: float) -> float:
    """What a goal counts for: its own budget, never above the manifest's per-goal cap."""
    return cap if budget_usd is None else min(budget_usd, cap)
