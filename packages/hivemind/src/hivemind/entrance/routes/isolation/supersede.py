"""Serve the human's Supersedure lever: move the Hive Stand, from loopback only (roadmap 13.2a).

``GET /v1/hive/supersede/{node_id}/preflight`` runs the candidate test and lists every check
(`hive doctor --supersedure`). ``POST /v1/hive/supersede`` moves the Hive Stand onto the node: it
is break-glass (codingrules 15, ADR-0065), so it needs an interactive device inside its step-up
window (``require_step_up``; a program cannot step up and is refused) and the typed phrase
``supersede my hive stand`` (``check_break_glass``), on top of ``entrance:steward``. Both rows are
mounted on the loopback listener only: Supersedure is the human at the Hive Stand. The order goes
through the Queen's door, which also passes the human-only `supersedure` point.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.isolation``.
    Registered in the route table. Calls into the step-up gate, the break-glass check, the Queen's
    door and ``hivemind.queen.supersedure``'s report types only.

Key invariants:
    - Neither row is ever mounted on the remote listener.
    - No Supersedure starts without an interactive, stepped-up session presenting the phrase.

See Also:
    - hivemind.queen.supersedure.door for what the lever does.
    - docs/runbooks/supersedure.md for the operator's view.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.auth.step_up import ActionKind, check_break_glass
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import LOOPBACK_ONLY, RouteEffect, RouteSpec, session_with
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.routes.isolation.cells import STEWARD
from hivemind.entrance.routes.isolation.sting_cut import MAX_PHRASE_CHARS
from hivemind.queen.supersedure import PreflightReport

_CONFIG = ConfigDict(frozen=True, extra="forbid")
_NODE_ID = r"^node_[0-9A-HJKMNP-TV-Z]{26}$"

NodePath = Annotated[str, Path(pattern=_NODE_ID, description="The candidate's node id.")]

__all__ = ["ROUTES", "PreflightView", "SupersedeBody", "SupersedeView", "preflight_view"]


class CheckView(BaseModel):
    """One preflight check."""

    model_config = _CONFIG

    name: str = Field(description="What was checked (enrolled, colonized, disk, ...).")
    outcome: str = Field(description="PASSED, FAILED or UNVERIFIED.")
    detail: str = Field(description="One line saying why.")


class PreflightView(BaseModel):
    """The candidate test for one node."""

    model_config = _CONFIG

    node_id: str = Field(description="The candidate.")
    passed: bool = Field(description="No check failed; a Supersedure onto it may start.")
    checks: list[CheckView] = Field(description="Every check, in a fixed order.")


class SupersedeBody(BaseModel):
    """Move the Hive Stand: the candidate and the typed phrase."""

    model_config = _CONFIG

    node_id: str = Field(pattern=_NODE_ID, description="The colonized node to move onto.")
    phrase: str = Field(
        min_length=1,
        max_length=MAX_PHRASE_CHARS,
        description="The break-glass phrase, typed: 'supersede my hive stand'.",
    )


class SupersedeView(BaseModel):
    """What the order started; the copy and the handover go on on the Queen's loop."""

    model_config = _CONFIG

    started_id: str = Field(description="The queen.supersedure_started event.")
    node_id: str = Field(description="The candidate.")
    sequence: int = Field(description="The QueenMoved sequence reserved for the move.")
    grace_until: str = Field(description="When the candidate must be ready, ISO 8601.")


def preflight_view(report: PreflightReport) -> PreflightView:
    """Shape the candidate test for the Landing Board.

    Args:
        report: The Queen's report.

    Returns:
        The view.
    """
    checks = [
        CheckView(name=c.name, outcome=c.outcome.value, detail=c.detail) for c in report.checks
    ]
    return PreflightView(node_id=report.node_id, passed=report.passed, checks=checks)


async def preflight(node_id: NodePath, caller: CallerParam, services: Services) -> PreflightView:
    """Run the candidate test for a node (read-only).

    Args:
        node_id: The candidate.
        caller: The admitted caller.
        services: The Entrance's services (the Queen's door).

    Returns:
        Every check.
    """
    del caller  # Admission (steward, loopback) is the whole gate for a read.
    return preflight_view(await services.queen.supersedure_preflight(node_id))


async def supersede(body: SupersedeBody, caller: CallerParam, services: Services) -> SupersedeView:
    """Move the Hive Stand on the human's order: interactive, after step-up, with the phrase.

    Args:
        body: The candidate and the phrase.
        caller: The admitted caller: an interactive device inside its step-up window.
        services: The Entrance's services (the Queen's door).

    Returns:
        What the order started.
    """
    await require_step_up(services, caller, ActionKind.SUPERSEDURE)
    check_break_glass(ActionKind.SUPERSEDURE, caller.session, body.phrase)
    # One Queen call: the point, the preflight, the freeze and the start; the rest is her loop's.
    started = await services.queen.supersede(body.node_id, caller.device.id)
    return SupersedeView(
        started_id=started.started_id,
        node_id=started.candidate_node_id,
        sequence=started.sequence,
        grace_until=started.grace_until.isoformat(),
    )


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/hive/supersede/{node_id}/preflight",
        listeners=LOOPBACK_ONLY,
        access=session_with(STEWARD),
        effect=RouteEffect.READ,
        endpoint=preflight,
        summary="Run the Supersedure candidate test for a node (loopback only).",
        response_model=PreflightView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/hive/supersede",
        listeners=LOOPBACK_ONLY,
        access=session_with(STEWARD),
        effect=RouteEffect.DOOR,
        endpoint=supersede,
        summary="Move the Hive Stand to a node (loopback, after step-up, with the typed phrase).",
        response_model=SupersedeView,
    ),
)
