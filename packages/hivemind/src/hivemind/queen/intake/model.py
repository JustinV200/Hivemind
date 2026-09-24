"""Define GoalRequest: one goal a human asked for, durable before the Hive acknowledges it.

Docs/adr/0032, "A goal is durable before it is acknowledged": `POST /v1/goals` writes one of these
in the Queen's own tables (the text, its budget, its requested Comb Shield tier, its origin, the
submitting device, the device's capability set as the goal's ceiling, and a state) and answers
`202` with its id only once the row is committed; the Queen then plans it herself, so a crash
after the `202` loses nothing. The row is also what Night Veil placement cites as the human
request that asked for the tier (docs/adr/0031), what "goal completed" is pushed to, and what a
device revocation's `--cancel-goals` reads. `GoalSource` says whether the human typed it or spoke
it (a spoken goal is usually echoed back before it becomes a task). The id is minted by
`hivemind.brood_chamber.task.new_goal_request_id` because every task of the goal stores it too.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's intake
    sub-package. Built by the Hive Entrance (a later step) or a test, committed by `Queen.
    request_goal`, moved only by `hivemind.queen.intake.writes`, persisted by a
    `GoalRequestStore`. Calls into `hivemind.brood_chamber.task` (the id and GoalCapabilities),
    `hivemind.cell` (tiers, clearance, origin), `hivemind.queen.intake.state` and waggle only.

Key invariants:
    - `goal_id` is set exactly when the state is PLANNED; `refusal` exactly when it is REFUSED;
      `finished_at` only on a PLANNED request; `confirmed_at` only on one that needed confirming.
    - A request for NIGHT_VEIL must come from a human (origin HUMAN, ADR-0031's floor): a Queen
      or Warden origin with that tier is refused at construction, before anything is stored.
    - `budget_usd` is the goal's own spend cap; the Queen only ever applies it as the lower of it
      and `[forage] spend_cap_per_goal_usd` (`hivemind.queen.intake.budget`), so it never widens.
    - The text is the human's own words: C2 by provenance (codingrules 8.9). It lives in this row
      and the planner's prompt, never on the trail or in a log line.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the row's fields.
    - hivemind.queen.intake.state for GoalRequestState and its transition table.
    - hivemind.queen.intake.writes for the functions that create and move one.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.brood_chamber.task import (
    GOAL_REQUEST_ID_PATTERN,
    MAX_OBJECTIVE_CHARS,
    GoalCapabilities,
    GoalRequestId,
)
from hivemind.cell import CombShieldLevel, HoneyClearance, RequestOrigin
from hivemind.queen.intake.state import GoalRequestState
from waggle.messages.base import DeviceIdField, TaskIdField, UtcDatetime

# Bounded like a goal: the planner turns these words into the goal task's own objective, so the
# same ceiling a TaskSpec.objective has keeps a request from ever outgrowing its plan.
MAX_GOAL_TEXT_CHARS = MAX_OBJECTIVE_CHARS
MAX_REFUSAL_CHARS = 2_000  # A paragraph: why it was refused, for the human; never a transcript.

__all__ = ["MAX_GOAL_TEXT_CHARS", "MAX_REFUSAL_CHARS", "GoalRequest", "GoalSource"]


class GoalSource(Enum):
    """How the human gave the goal: typed, or spoken and transcribed (roadmap step 10.5f)."""

    TYPED = "typed"  # Typed on a device; planned as soon as the Queen reaches it.
    SPOKEN = "spoken"  # Transcribed speech; usually echoed back for confirmation first.


class GoalRequest(BaseModel):
    """One durable goal request: what the human asked for, and how far the Queen has got with it.

    Crosses a boundary twice: the Hive Entrance builds it from a device's request body, and the
    Queen's goal-request store writes it as one row's JSON body; every change is a new value
    (`model_copy` through `hivemind.queen.intake.writes`), never an in-place edit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: GoalRequestId = Field(
        pattern=GOAL_REQUEST_ID_PATTERN,
        description="This request's own id, a goalreq_-prefixed ULID minted with the Hive clock.",
    )
    text: str = Field(
        min_length=1,
        max_length=MAX_GOAL_TEXT_CHARS,
        description="The goal in the human's own words (C2 by provenance); never on the trail.",
    )
    budget_usd: float | None = Field(
        default=None,
        gt=0,
        description="The goal's own spend cap in US dollars; it only ever lowers [forage] "
        "spend_cap_per_goal_usd. None means the manifest's cap alone.",
    )
    comb_shield: CombShieldLevel | None = Field(
        default=None,
        description="The Comb Shield tier the human asked for; every planned task needs it. "
        "Only a human request may name NIGHT_VEIL (ADR-0031). None leaves tiers to the planner.",
    )
    clearance: HoneyClearance = Field(
        description="The goal's data-sensitivity ceiling; every planned task is at or below it."
    )
    origin: RequestOrigin = Field(
        default=RequestOrigin.HUMAN,
        description="Who asked for the goal; HUMAN for everything the Hive Entrance accepts.",
    )
    device_id: DeviceIdField | None = Field(
        default=None,
        description="The enrolled device that submitted it; None for the operator's local CLI.",
    )
    capabilities: GoalCapabilities = Field(
        default=None,
        description="The submitting device's capability set, the goal's ceiling (ADR-0031), as "
        "sorted capability strings; None for the operator's own path, with no device ceiling.",
    )
    source: GoalSource = Field(default=GoalSource.TYPED, description="Typed or spoken.")
    needs_confirmation: bool = Field(
        default=False,
        description="True when the Queen must echo it back and wait for the human's yes before "
        "planning (a spoken goal under [entrance.voice] confirm_goals, or a step-up hold).",
    )
    state: GoalRequestState = Field(
        default=GoalRequestState.RECEIVED,
        description="Where it is in hivemind.queen.intake.state's transition table.",
    )
    goal_id: TaskIdField | None = Field(
        default=None, description="The planned goal's id (its first task); set once PLANNED."
    )
    refusal: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_REFUSAL_CHARS,
        description="Why it will never be planned, for the human; set once REFUSED.",
    )
    received_at: UtcDatetime = Field(description="When the row was first committed.")
    updated_at: UtcDatetime = Field(description="When the row last changed.")
    confirmed_at: UtcDatetime | None = Field(
        default=None, description="When the human confirmed it, for one that needed confirming."
    )
    finished_at: UtcDatetime | None = Field(
        default=None,
        description="When every task of its planned goal was first seen terminal; the Queen "
        "tells the device once, then sets this so it never tells it twice.",
    )

    @model_validator(mode="after")
    def _validate_request(self) -> GoalRequest:
        """Run every cross-field invariant; split into helpers so each stays small and readable."""
        _check_outcome_fields(self)
        _check_confirmation(self)
        _check_night_veil_origin(self)
        _check_times(self)
        return self


def _check_outcome_fields(request: GoalRequest) -> None:
    """Require goal_id iff PLANNED, refusal iff REFUSED, and finished_at only once PLANNED."""
    planned = request.state is GoalRequestState.PLANNED
    if planned != (request.goal_id is not None):
        raise ValueError(
            f"Goal request {request.id} is {request.state.value}: goal_id is set exactly when "
            "the state is PLANNED."
        )
    if (request.state is GoalRequestState.REFUSED) != (request.refusal is not None):
        raise ValueError(
            f"Goal request {request.id} is {request.state.value}: refusal is set exactly when "
            "the state is REFUSED."
        )
    if request.finished_at is not None and not planned:
        raise ValueError(f"Goal request {request.id} can only finish once PLANNED.")


def _check_confirmation(request: GoalRequest) -> None:
    """Refuse a confirmation time on a request that never needed confirming."""
    if request.confirmed_at is not None and not request.needs_confirmation:
        raise ValueError(
            f"Goal request {request.id} was never held for confirmation, so it cannot carry "
            "confirmed_at."
        )


def _check_night_veil_origin(request: GoalRequest) -> None:
    """Refuse NIGHT_VEIL from anyone but a human: ADR-0031's initiation floor, structurally."""
    if request.comb_shield is CombShieldLevel.NIGHT_VEIL and request.origin is not (
        RequestOrigin.HUMAN
    ):
        raise ValueError(
            f"Goal request {request.id} asks for NIGHT_VEIL with origin {request.origin.value}: "
            "only a human request may initiate Night Veil work (ADR-0031)."
        )


def _check_times(request: GoalRequest) -> None:
    """Refuse an updated_at earlier than the request's own received_at."""
    if request.updated_at < request.received_at:
        raise ValueError(
            f"Goal request {request.id} updated_at ({request.updated_at}) precedes received_at "
            f"({request.received_at})."
        )
