"""Define the goals resource's bodies: submitting a goal, and reading how far it has got.

A goal submitted at the Landing Board becomes a durable ``GoalRequest`` in the Queen's own tables
before the Entrance answers ``202`` (ADR-0040); the Queen plans it on her own tick. A submission
names the goal in the human's words, and optionally a budget (it only ever lowers the manifest's
per-goal cap), a Comb Shield tier (``night_veil`` needs ``cell:comb_shield:night_veil``) and a
clearance. ``GoalView`` is the request's progress without its text, so a program that may submit but
not read personal content can still follow its own goals.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.goals`` and the confirmation route; published in the OpenAPI
    document. Calls into the Queen's goal-request model and pydantic.

Key invariants:
    - A view never carries the goal's text or its refusal (both are the human's, C2).

See Also:
    - hivemind.queen.intake for the goal request and its state machine.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.queen import GoalRequest, GoalRequestState, GoalSource
from hivemind.queen.intake import MAX_GOAL_TEXT_CHARS
from waggle.messages.base import TaskIdField

MAX_REASON_CHARS = 500  # Why the human declined: a sentence or two.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = ["DeclineBody", "GoalAccepted", "GoalSubmission", "GoalView", "goal_view"]


class GoalSubmission(BaseModel):
    """Submit a goal (``entrance:submit``)."""

    model_config = _CONFIG

    text: str = Field(
        min_length=1, max_length=MAX_GOAL_TEXT_CHARS, description="The goal, in the human's words."
    )
    budget_usd: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="The goal's own spend cap in USD; it only lowers [forage] "
        "spend_cap_per_goal_usd. Above [entrance] step_up_spend it needs a step-up.",
    )
    comb_shield: CombShieldLevel | None = Field(
        default=None,
        description="The Comb Shield tier every task needs; NIGHT_VEIL needs "
        "cell:comb_shield:night_veil. Null leaves tiers to the planner.",
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1,
        description="The goal's data-sensitivity ceiling; every task is at or below it.",
    )


class GoalAccepted(BaseModel):
    """A goal request committed in the Queen's tables (``202 Accepted``)."""

    model_config = _CONFIG

    id: str = Field(description="The goal request's id (goalreq_...); follow it with GET.")
    state: GoalRequestState = Field(description="RECEIVED: the Queen plans it on her own tick.")


class GoalView(BaseModel):
    """A goal request's progress, without its text."""

    model_config = _CONFIG

    id: str = Field(description="The goal request's id.")
    state: GoalRequestState = Field(
        description="RECEIVED, AWAITING_CONFIRMATION, PLANNING, PLANNED or REFUSED."
    )
    goal_id: TaskIdField | None = Field(description="The planned goal's id, once PLANNED.")
    budget_usd: float | None = Field(description="Its own spend cap, if it named one.")
    comb_shield: CombShieldLevel | None = Field(description="The tier it asked for, if any.")
    clearance: HoneyClearance = Field(description="Its data-sensitivity ceiling.")
    source: GoalSource = Field(description="typed or spoken.")
    needs_confirmation: bool = Field(description="It waits for the human's yes before planning.")
    refused: bool = Field(description="It will never be planned (the reason is in the chat).")
    received_at: datetime = Field(description="When it was committed.")
    updated_at: datetime = Field(description="When it last changed.")
    confirmed_at: datetime | None = Field(description="When the human confirmed it, if asked.")
    finished_at: datetime | None = Field(description="When every task of its goal finished.")


class DeclineBody(BaseModel):
    """Decline a goal request held for the human's yes."""

    model_config = _CONFIG

    reason: str = Field(
        default="declined by the human",
        min_length=1,
        max_length=MAX_REASON_CHARS,
        description="Why, kept on the request for the human (never on the trail).",
    )


def goal_view(request: GoalRequest) -> GoalView:
    """Shape a goal request for the Landing Board, leaving its text behind.

    Args:
        request: The request as the Queen's tables hold it.

    Returns:
        Its view.
    """
    return GoalView(
        id=request.id,
        state=request.state,
        goal_id=request.goal_id,
        budget_usd=request.budget_usd,
        comb_shield=request.comb_shield,
        clearance=request.clearance,
        source=request.source,
        needs_confirmation=request.needs_confirmation,
        refused=request.state is GoalRequestState.REFUSED,
        received_at=request.received_at,
        updated_at=request.updated_at,
        confirmed_at=request.confirmed_at,
        finished_at=request.finished_at,
    )
