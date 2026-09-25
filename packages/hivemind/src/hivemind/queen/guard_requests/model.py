"""Define GuardRequest: one Guard Bee request as the Queen's own table holds it, and her decision.

A Guard request (ADR-0043) is a `hivemind.guard.GuardReport` whose recommended action asks the
Queen to act on one Cell or one bee. It reaches her through her door
(`hivemind.queen.guard_requests.door`), which writes one `GuardRequest` row before it returns, so a
request filed just before a restart is still decided after it. The row carries the report exactly
as filed, when it was filed, and, once she has decided, her `GuardDecision`: the `QueenAction`
(isolate the Cell, quarantine the bee, dismiss it, or put it in front of the human), what the
decision rested on (`GuardBasis`: a dire pattern's autopilot rule, an awake episode, or the
fallback when her awake mode is unavailable), the `queen.decided` event that recorded it and what
came of it. A decision about the Hive Stand, whose own lease only the human may isolate, may also
leave a `PlacementHold`: the goals of the implicated tasks are not placed on that Cell again until
the human lifts it (roadmap step 10.6a), which placement reads as data. Nothing here holds content:
the report is ids, a rule key, counts and the rule's own one-line summary.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Built by the door and the Queen's decision on a request
    (`hivemind.queen.guard_requests.decision`); stored by `GuardRequestStore`; read by the
    dispatcher's placement snapshot (the holds) and the isolation lift (releasing them). Calls into
    `hivemind.guard` (GuardReport), `hivemind.queen.autopilot` (QueenAction) and pydantic only.

Key invariants:
    - A row is created only for a report that asks for something (`GuardReport.is_request`).
    - `decision` is set at most once; a hold exists only on a decided row and is released at most
      once.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.guard.report for GuardReport, the contract this row carries unchanged.
"""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.guard import GuardReport, GuardReportId
from hivemind.queen.autopilot import QueenAction
from waggle.messages.base import CellIdField, EventIdField, TaskIdField, UtcDatetime

MAX_OUTCOME_CHARS = 64  # A short snake_case word for what came of a decision, never prose.
MAX_HELD_GOALS = 32  # Every goal one report's tasks belong to (a report names at most 32 tasks).

__all__ = [
    "MAX_HELD_GOALS",
    "MAX_OUTCOME_CHARS",
    "GuardBasis",
    "GuardDecision",
    "GuardRequest",
    "PlacementHold",
]


class GuardBasis(Enum):
    """What the Queen's decision on a Guard request rested on."""

    RULE = "rule"  # A dire pattern's autopilot rule (`[guard] dire_patterns`): no model asked.
    AWAKE = "awake"  # One awake episode with the report's facts attached.
    FALLBACK = "fallback"  # Her awake mode was unavailable: isolate, which only removes access.


class GuardDecision(BaseModel):
    """The Queen's one decision on a Guard request, and what came of carrying it out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: QueenAction = Field(
        description="ISOLATE_CELL, QUARANTINE_BEE, DISMISS or ESCALATE_TO_HUMAN."
    )
    basis: GuardBasis = Field(description="A rule, an awake episode, or the fallback.")
    event_id: EventIdField = Field(description="The queen.decided event that recorded it.")
    decided_at: UtcDatetime = Field(description="When she decided.")
    outcome: str = Field(
        min_length=1,
        max_length=MAX_OUTCOME_CHARS,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="What came of it: isolated, quarantine_ordered, hive_stand_fallback, "
        "dismissed, escalated, or why it could not be carried out.",
    )


class PlacementHold(BaseModel):
    """Goals the Queen will not place on one Cell until the human lifts the hold (roadmap 10.6a).

    The Hive Stand's own lease is isolated only by the human; on a dire pattern there the Queen
    quarantines the implicated bee and holds its goal off the Hive Stand instead. Placement reads
    an unreleased hold as a `BLOCK` on that Cell for those goals alone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: GuardReportId = Field(description="The Guard report whose decision held them.")
    cell_id: CellIdField = Field(description="The Cell they are held off (the Hive Stand).")
    goal_ids: tuple[TaskIdField, ...] = Field(
        min_length=1, max_length=MAX_HELD_GOALS, description="The goals held off that Cell."
    )
    held_at: UtcDatetime = Field(description="When the hold was decided.")
    released_at: UtcDatetime | None = Field(
        default=None, description="When the human's lift released it; None while it holds."
    )

    @property
    def is_active(self) -> bool:
        """Whether placement still honours this hold (not yet released)."""
        return self.released_at is None


class GuardRequest(BaseModel):
    """One filed Guard request: the report as filed, when, and the Queen's decision once made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report: GuardReport = Field(description="The Guard Bee's report, exactly as filed.")
    filed_at: UtcDatetime = Field(description="When the door wrote it; its age in her inbox.")
    decision: GuardDecision | None = Field(
        default=None, description="Her decision; None while the request waits for her tick."
    )
    hold: PlacementHold | None = Field(
        default=None, description="The placement hold her decision left, if it left one."
    )

    @model_validator(mode="after")
    def _is_a_request_decided_before_it_holds(self) -> Self:
        """Refuse a row for a report that asks nothing, or a hold on an undecided request."""
        # A finding that asks for nothing is the Guard Bee's alone to record (guard.alert); it
        # never enters the Queen's inbox, so no row may hold one.
        if not self.report.is_request:
            raise ValueError(f"Report {self.report.id} asks the Queen for nothing.")
        if self.hold is not None and (self.decision is None or self.hold.report_id != self.id):
            raise ValueError(f"A hold on request {self.id} needs its own decision first.")
        return self

    @property
    def id(self) -> GuardReportId:
        """The request's id: its report's, since a report is filed at most once."""
        return self.report.id

    @property
    def is_pending(self) -> bool:
        """Whether the Queen has yet to decide it."""
        return self.decision is None
