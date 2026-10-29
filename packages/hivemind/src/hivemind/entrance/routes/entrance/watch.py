"""Serve the owner's yes or no to a task that would watch them: list, approve and deny.

ADR-0069: a Worker drives the operator's running screen or audio only after the owner approved
that task on that Cell. The dispatcher holds such a task PENDING and files a watch request; these
rows show what waits and carry the owner's answer to the Queen's door (``QueenDoor``), which never
lets the Entrance write her book itself. Like enrolment (ADR-0041), the list is readable from
either listener by a steward, but approving and denying are loopback-only rows: never mounted on
the remote listener (a remote device, the one that would be watched included, gets 404), and
refused with a bare 403 without the Hive Stand's per-boot stand key (ADR-0068), so a Virtual
Cell's guest or any keyless program on the Hive Stand cannot answer, even inside a console
session. Each row also needs ``entrance:steward``, the human's family.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.entrance``.
    Registered in the route table. Calls into the Queen's door and ``hivemind.queen.peripherals``
    (the request it shapes) only.

Key invariants:
    - No answer is served on the remote listener or taken without the stand key.
    - The list shows each request's task title (planner text, so C2) and nothing of its objective.

See Also:
    - hivemind.queen.peripherals for the requests, the hold and the door.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    LOOPBACK_ONLY,
    RouteEffect,
    RouteSpec,
    session_with,
)
from hivemind.queen.peripherals import WATCH_ID_PATTERN, WatchRequest

STEWARD = "entrance:steward"  # Saying who may watch the owner is the human's alone (ADR-0039).
_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

WatchIdPath = Annotated[str, Path(pattern=WATCH_ID_PATTERN, description="The watch request.")]

__all__ = ["ROUTES", "WatchRequestList", "WatchRequestView"]


class WatchRequestView(BaseModel):
    """One task's ask to watch the owner on one Cell, as the owner decides on it."""

    model_config = _CONFIG

    id: str = Field(description="The request.")
    task_id: str = Field(description="The task that would hold the scopes.")
    title: str = Field(description="The task's one-line title, as the planner wrote it.")
    cell_id: str = Field(description="The Cell that would be watched.")
    cell_name: str = Field(description="That Cell's name.")
    warden_id: str = Field(description="The Warden that would spawn the Worker.")
    role: str = Field(description="The Worker role it would spawn (DRONE, FORAGER, ...).")
    scopes: list[str] = Field(description="exoskeleton:real_display and/or exoskeleton:audio.")
    requested_at: datetime = Field(description="When the task asked.")
    expires_at: datetime = Field(description="When an unanswered request refuses the task.")
    status: str = Field(description="PENDING, APPROVED, DENIED or EXPIRED.")


class WatchRequestList(BaseModel):
    """Every watch request still waiting for the owner, oldest first."""

    model_config = _CONFIG

    requests: list[WatchRequestView] = Field(description="The waiting requests.")


def _view(request: WatchRequest) -> WatchRequestView:
    """Shape one Queen request for the Landing Board."""
    return WatchRequestView(
        id=request.id,
        task_id=request.task_id,
        title=request.title,
        cell_id=request.cell_id,
        cell_name=request.cell_name,
        warden_id=request.warden_id,
        role=request.role.value,
        scopes=list(request.scopes),
        requested_at=request.requested_at,
        expires_at=request.expires_at,
        status=request.status.value,
    )


async def list_waiting(services: Services) -> WatchRequestList:
    """List the tasks waiting for the owner's yes to watch them.

    Args:
        services: The Entrance's services (the Queen's door).

    Returns:
        Every PENDING request, oldest first.
    """
    # Latency: one in-memory read of the Queen's book.
    pending = services.queen.pending_watch_requests()
    return WatchRequestList(requests=[_view(request) for request in pending])


async def approve(
    request_id: WatchIdPath, caller: CallerParam, services: Services
) -> WatchRequestView:
    """Approve one task's watch request, from the Hive Stand.

    Args:
        request_id: The request.
        caller: The admitted caller: a steward session carrying the stand key.
        services: The Entrance's services.

    Returns:
        The request, APPROVED; the task is assigned on the Queen's next pass.

    Raises:
        UnknownWatchRequestError: No waiting task has that request (404).
        WatchRequestSettledError: It was already answered, or it expired (409).
    """
    answered = await services.queen.answer_watch_request(request_id, True, caller.device.id)
    return _view(answered)


async def deny(
    request_id: WatchIdPath, caller: CallerParam, services: Services
) -> WatchRequestView:
    """Deny one task's watch request, from the Hive Stand; the task is cancelled.

    Args:
        request_id: The request.
        caller: The admitted caller: a steward session carrying the stand key.
        services: The Entrance's services.

    Returns:
        The request, DENIED.

    Raises:
        UnknownWatchRequestError: No waiting task has that request (404).
        WatchRequestSettledError: It was already answered, or it expired (409).
    """
    answered = await services.queen.answer_watch_request(request_id, False, caller.device.id)
    return _view(answered)


_STEWARD = session_with(STEWARD)
_REQUEST = "/v1/entrance/watch/{request_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/entrance/watch",
        listeners=BOTH_LISTENERS,
        access=session_with(STEWARD, c2=True),
        effect=RouteEffect.READ,
        endpoint=list_waiting,
        summary="List the tasks waiting for the owner's yes to drive the running screen or "
        "audio (C2).",
        response_model=WatchRequestList,
    ),
    RouteSpec(
        method="POST",
        path=f"{_REQUEST}/approve",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=approve,
        summary="Approve one task's use of the running screen or audio on one Cell (loopback "
        "only, with the stand key).",
        response_model=WatchRequestView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_REQUEST}/deny",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=deny,
        summary="Deny one task's use of the running screen or audio; the task is cancelled "
        "(loopback only, with the stand key).",
        response_model=WatchRequestView,
    ),
)
