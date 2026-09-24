"""Serve the requests held for a person: list them, confirm one, or decline it.

A device no person types at cannot step up, so what it asks for that needs step-up waits as a
pending confirmation (ADR-0033): a program's goal above its spend cap, or the network the travel
lock flagged. A person lists them (``C2``: the payload is shown, so they know what they confirm)
and confirms one only from an interactive device inside its step-up window; the confirmation is
settled once, and the held action is carried out exactly once: a goal is committed under the id
minted when it was held (a retry finds it committed), a network is trusted for the device.
Declining needs no step-up.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.entrance``.
    Registered in the route table. Calls into the confirmation flow, the goals route's
    ``submit_held_goal`` and the travel lock.

Key invariants:
    - Only an interactive, stepped-up session confirms; a held action is carried out at most once.

See Also:
    - hivemind.entrance.auth.confirm for the flow and its state machine.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.confirm import HeldAction, PendingId, PendingStatus, cancel, confirm
from hivemind.entrance.auth.step_up import ActionKind, StepUpReason
from hivemind.entrance.gate.errors import StepUpRequiredError
from hivemind.entrance.gate.params import CallerParam, Here, Services
from hivemind.entrance.gate.services import EntranceServices, ListenerDeps
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.models import (
    ConfirmationList,
    ConfirmationView,
    ConfirmedView,
    confirmation_view,
)
from hivemind.entrance.routes.goals import submit_held_goal

SUBMIT = "entrance:submit"  # A person who may give the Hive work confirms what waits for them.

PendingIdPath = Annotated[
    str, Path(pattern=r"^pend_[0-9A-HJKMNP-TV-Z]{26}$", description="The held request's id.")
]

log = get_logger(__name__)

__all__ = ["ROUTES"]


async def list_held(services: Services) -> ConfirmationList:
    """List every request still waiting for a person (C2: payloads included).

    Args:
        services: The Entrance's services.

    Returns:
        The PENDING confirmations, oldest first.
    """
    # Latency: one local read of the pending table.
    held = await services.enrolment.records.store.pending.list_by_status(PendingStatus.PENDING)
    return ConfirmationList(confirmations=[confirmation_view(pending) for pending in held])


async def confirm_held(
    pending_id: PendingIdPath, caller: CallerParam, here: Here, services: Services
) -> ConfirmedView:
    """Confirm a held request and carry it out, once.

    Args:
        pending_id: The held request.
        caller: The admitted caller: interactive, inside its step-up window.
        here: This listener's dependencies (the travel lock).
        services: The Entrance's services.

    Returns:
        What was carried out.

    Raises:
        StepUpRequiredError: An interactive device that has not stepped up yet.
    """
    # Tell a person who has not stepped up to do so; the flow refuses anyone else outright.
    if caller.session.interactive and not caller.session.stepped_up:
        raise StepUpRequiredError(StepUpReason.SENSITIVE_ACTION)
    held = await confirm(services.enrolment.records, PendingId(pending_id), caller.session)
    return await _carry_out(held, services, here)


async def cancel_held(
    pending_id: PendingIdPath, caller: CallerParam, services: Services
) -> ConfirmationView:
    """Decline a held request (an interactive device; no step-up needed).

    Args:
        pending_id: The held request.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The confirmation, CANCELLED.
    """
    records = services.enrolment.records
    return confirmation_view(await cancel(records, PendingId(pending_id), caller.session))


async def _carry_out(
    held: HeldAction, services: EntranceServices, here: ListenerDeps
) -> ConfirmedView:
    """Carry out a confirmed request: commit its goal, or trust its network."""
    answer = ConfirmedView(pending_id=held.pending_id, action=held.action)
    if held.action is ActionKind.GOAL:
        # Latency: one local read (the device as it stands), one local transaction.
        device = await services.enrolment.records.store.get_device(held.device_id)
        goal_request_id = await submit_held_goal(services, device, held.payload)
        return answer.model_copy(update={"goal_request_id": goal_request_id})
    travel = here.auth.guards.travel
    if held.action is ActionKind.NEW_NETWORK and travel is not None:
        network = held.payload.get("network")
        await travel.trust(held.device_id, network if isinstance(network, str) else None)
        return answer
    # Nothing else is ever held by a route; settling it was all there was to do.
    log.warning(
        "entrance.held_action_unhandled", pending_id=held.pending_id, action=held.action.value
    )
    return answer


_SUBMIT = session_with(SUBMIT)
_HELD = "/v1/entrance/confirmations/{pending_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/entrance/confirmations",
        listeners=BOTH_LISTENERS,
        access=session_with(SUBMIT, c2=True),
        effect=RouteEffect.READ,
        endpoint=list_held,
        summary="List the requests held for a person's confirmation (C2).",
        response_model=ConfirmationList,
    ),
    RouteSpec(
        method="POST",
        path=f"{_HELD}/confirm",
        listeners=BOTH_LISTENERS,
        access=_SUBMIT,
        effect=RouteEffect.DOOR,
        endpoint=confirm_held,
        summary="Confirm a held request from an interactive device inside its step-up window.",
        response_model=ConfirmedView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_HELD}/cancel",
        listeners=BOTH_LISTENERS,
        access=_SUBMIT,
        effect=RouteEffect.DOOR,
        endpoint=cancel_held,
        summary="Decline a held request.",
        response_model=ConfirmationView,
    ),
)
