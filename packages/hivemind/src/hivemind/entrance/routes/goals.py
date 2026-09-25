"""Serve the goals resource: submit a goal, read how far it got, confirm or decline a held one.

``POST /v1/goals`` is how a device gives the Hive work (ADR-0040). The goal becomes a durable
``GoalRequest`` in the Queen's own tables, carrying the device's approved set as its ceiling
(ADR-0039), and the Entrance answers ``202`` with its id only after the row is committed; the Queen
plans it on her own tick. Before that, ADR-0041's step-up rules apply: a budget above
``step_up_spend``, or a goal that would take the device past its daily cap (weighed over the goals
it submitted in the last day, each counted at its budget or the manifest's per-goal cap), needs a
step-up; a program cannot step up, so its goal is held as a pending confirmation, submitted exactly
once when a person confirms it (under the goal request id minted now, so a retry never submits it
twice). A Night Veil goal needs ``cell:comb_shield:night_veil``. The weighing and the commit are
``hivemind.entrance.intake``'s, shared with the voice door and the confirmation route. A goal held
for the human's yes (a spoken one echoed back) is confirmed or declined here, by a person.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table. Calls into the Queen's door and goal-request table, the Entrance's intake,
    and the gate's step-up and capability checks.

Key invariants:
    - A ``202`` is answered only after the request's row and event are committed.
    - A held goal is submitted at most once, under the id minted when it was held.
    - A device reads only the goal requests it submitted.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md, "A goal is durable before
      it is acknowledged".
    - hivemind.queen.intake for the goal request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import JsonValue

from hivemind.cell import CombShieldLevel
from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.errors import ConfirmationRefusedError
from hivemind.entrance.gate.admit import authorise
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.intake import goal_spend, submit_held_goal
from hivemind.entrance.models import (
    DeclineBody,
    GoalAccepted,
    GoalSubmission,
    GoalView,
    goal_view,
)
from hivemind.queen import GoalRequestState
from hivemind.queen.intake import (
    GOAL_REQUEST_ID_PATTERN,
    GoalRequestNotFoundError,
    new_goal_request_id,
)
from waggle.ids import DeviceId

SUBMIT = "entrance:submit"  # The capability every goal route needs.
NIGHT_VEIL_CAPABILITY = "cell:comb_shield:night_veil"  # Only a device holding it asks for it.

GoalRequestIdPath = Annotated[str, Path(pattern=GOAL_REQUEST_ID_PATTERN, max_length=64)]

__all__ = ["ROUTES"]


async def submit_goal(
    body: GoalSubmission, caller: CallerParam, services: Services
) -> GoalAccepted:
    """Commit a goal request, after the Night Veil and step-up rules; answer ``202``.

    Args:
        body: The goal, its budget, tier and clearance.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The goal request's id, once its row is committed.
    """
    # ADR-0039: only a device holding the Night Veil tier may ask for it; a refusal is trailed.
    if body.comb_shield is CombShieldLevel.NIGHT_VEIL:
        await authorise(services, caller, NIGHT_VEIL_CAPABILITY)
    request_id = new_goal_request_id(services.clock)
    held = _held_payload(body, request_id)
    spend = await goal_spend(services, caller.device, body.budget_usd)
    await require_step_up(services, caller, ActionKind.GOAL, spend, held)
    committed = await submit_held_goal(services, caller.device, held)
    return GoalAccepted(id=committed, state=GoalRequestState.RECEIVED)


async def read_goal(
    request_id: GoalRequestIdPath, caller: CallerParam, services: Services
) -> GoalView:
    """Read how far one of the caller's own goal requests has got (without its text).

    Args:
        request_id: The goal request.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        Its progress.

    Raises:
        GoalRequestNotFoundError: No such request, or another device submitted it.
    """
    request = await services.hive.goal_requests.get(request_id)
    # Another device's goal is not the caller's to read: it answers as if it did not exist.
    if request.device_id != caller.device.id:
        raise GoalRequestNotFoundError(request_id)
    return goal_view(request)


async def confirm_goal(
    request_id: GoalRequestIdPath, caller: CallerParam, services: Services
) -> GoalView:
    """Confirm a goal request held for the human's yes (an interactive device).

    Args:
        request_id: The goal request.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The request, RECEIVED again: the Queen plans it next.
    """
    _require_person(caller.session.interactive, caller.device.id)
    return goal_view(await services.queen.confirm_goal_request(request_id))


async def decline_goal(
    request_id: GoalRequestIdPath, body: DeclineBody, caller: CallerParam, services: Services
) -> GoalView:
    """Decline a goal request held for the human's yes (an interactive device).

    Args:
        request_id: The goal request.
        body: Why.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The request, REFUSED.
    """
    _require_person(caller.session.interactive, caller.device.id)
    return goal_view(await services.queen.decline_goal_request(request_id, body.reason))


def _require_person(interactive: bool, device_id: DeviceId) -> None:
    """Refuse a device no person types at: a held goal waits for the human's own yes or no."""
    if not interactive:
        raise ConfirmationRefusedError(
            f"Device {device_id} is not interactive; a person confirms or declines a held goal."
        )


def _held_payload(body: GoalSubmission, request_id: str) -> dict[str, JsonValue]:
    """Everything needed to commit the goal later: its minted id and the submission."""
    return {
        "id": request_id,
        "text": body.text,
        "budget_usd": body.budget_usd,
        "comb_shield": body.comb_shield.value if body.comb_shield is not None else None,
        "clearance": body.clearance.value,
    }


_SUBMIT = session_with(SUBMIT)
_GOAL_PATH = "/v1/goals/{request_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/goals",
        listeners=BOTH_LISTENERS,
        access=_SUBMIT,
        effect=RouteEffect.INBOX,
        endpoint=submit_goal,
        summary="Submit a goal; 202 once it is committed in the Queen's tables.",
        status_code=202,
        response_model=GoalAccepted,
    ),
    RouteSpec(
        method="GET",
        path=_GOAL_PATH,
        listeners=BOTH_LISTENERS,
        access=_SUBMIT,
        effect=RouteEffect.READ,
        endpoint=read_goal,
        summary="Read one of your goal requests, without its text.",
        response_model=GoalView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_GOAL_PATH}/confirm",
        listeners=BOTH_LISTENERS,
        access=_SUBMIT,
        effect=RouteEffect.INBOX,
        endpoint=confirm_goal,
        summary="Confirm a goal held for the human's yes.",
        response_model=GoalView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_GOAL_PATH}/decline",
        listeners=BOTH_LISTENERS,
        access=_SUBMIT,
        effect=RouteEffect.INBOX,
        endpoint=decline_goal,
        summary="Decline a goal held for the human's yes.",
        response_model=GoalView,
    ),
)
