"""Define IsolationOrder and what isolating or lifting a Cell reports back.

Isolation (roadmap step 10.6a, ADR-0043) is the Queen's action on one Cell (a unit of compute:
a Virtual Cell the Hive provisioned, or a Real Cell it borrows), never a Guard Bee's or a
Warden's. An `IsolationOrder` is one such isolation as data: the Cell, who ordered it (the Queen,
deciding a Guard request or following her own escalation policy, or the human at the Entrance,
the only one who may isolate the Hive Stand), why, the Guard report it answers if any, and the
trail ids that justified it, whose first one is where the Cell's memory stops being trusted. The
one isolation path (`hivemind.queen.isolation.path`) carries it out and answers with an
`IsolationOutcome`: isolated (with what it paused, revoked, wrote and cut), found already
isolated, or refused (and why, so her decision on a Hive Stand request can fall back to the
quarantine the ADR prescribes). `LiftOutcome` is what the human's lift did.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Built by the Queen's decision on a Guard request, her Alarm handling
    and her isolation door (the human's lever); read by the path, the lift and the Hive Entrance's
    replies. Calls into `hivemind.guard` (GuardReportId), `hivemind.hive` (EgressOutcome) and
    pydantic/waggle only.

Key invariants:
    - Every model is frozen and forbids extras; every string is bounded; ids only, never content.
    - An outcome is `isolated` exactly when it carries the `cell.isolated` event id.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.queen.isolation.path for the one code path.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.guard import GuardReportId
from hivemind.hive import EgressOutcome
from waggle.messages.base import (
    CellIdField,
    DeviceIdField,
    EventIdField,
    GrantIdField,
    TaskIdField,
    UtcDatetime,
)

MAX_ISOLATION_REASON_CHARS = 300  # A sentence naming ids, like every trail reason in the Hive.
MAX_EVIDENCE_EVENTS = 66  # A report's 64 cited events, plus the Alarm and the decision behind it.

__all__ = [
    "MAX_EVIDENCE_EVENTS",
    "MAX_ISOLATION_REASON_CHARS",
    "IsolationOrder",
    "IsolationOutcome",
    "IsolationRefusal",
    "Isolator",
    "LiftOutcome",
]

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class Isolator(Enum):
    """Who ordered an isolation."""

    QUEEN = "queen"  # Her decision on a Guard request, or her own policy's ISOLATE row.
    HUMAN = "human"  # The human's own lever at the Hive Entrance: the only way onto the Hive Stand.


class IsolationRefusal(Enum):
    """Why an isolation did not happen."""

    HIVE_STAND = (
        "hive_stand"  # The Queen may not isolate the Hive Stand's own lease; only the human.
    )
    NOT_HELD = "not_held"  # The orderer does not hold the Cell's own capability.


class IsolationOrder(BaseModel):
    """One isolation to carry out: the Cell, who ordered it, why, and on what evidence."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="The Cell to isolate.")
    ordered_by: Isolator = Field(description="The Queen, or the human.")
    reason: str = Field(
        min_length=1, max_length=MAX_ISOLATION_REASON_CHARS, description="Why, naming ids only."
    )
    report_id: GuardReportId | None = Field(
        default=None, description="The Guard report the isolation answers, if any."
    )
    evidence: tuple[EventIdField, ...] = Field(
        default=(),
        max_length=MAX_EVIDENCE_EVENTS,
        description="The trail ids that justified it, oldest first: the Cell's memory is tainted "
        "from the first of them on (ADR-0043).",
    )
    decision_event_id: EventIdField | None = Field(
        default=None, description="The queen.decided event that decided it, for her orders."
    )
    device_id: DeviceIdField | None = Field(
        default=None, description="The enrolled device the human ordered it from, for theirs."
    )


class IsolationOutcome(BaseModel):
    """What one isolation did: isolated the Cell, found it isolated already, or was refused."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="The Cell named.")
    event_id: EventIdField | None = Field(
        default=None, description="The cell.isolated event; None when nothing was isolated now."
    )
    already_isolated: bool = Field(
        default=False, description="The Cell was isolated before this order; nothing changed."
    )
    refusal: IsolationRefusal | None = Field(
        default=None, description="Why the isolation point refused it, when it did."
    )
    wax_id: str | None = Field(default=None, description="The BLOCK Cell Wax it wrote.")
    revoked_grant_ids: tuple[GrantIdField, ...] = Field(
        default=(), description="The Warden's grants it revoked."
    )
    paused_task_ids: tuple[TaskIdField, ...] = Field(
        default=(), description="The Cell's tasks it paused."
    )
    unacknowledged_task_ids: tuple[TaskIdField, ...] = Field(
        default=(), description="Paused tasks whose bee did not acknowledge within the bound."
    )
    egress: EgressOutcome = Field(
        default=EgressOutcome.UNTRACKED, description="What happened to the Cell's egress."
    )
    suspect_at: UtcDatetime | None = Field(
        default=None,
        description="From when the Cell's memory is suspect: its first evidence, or the start.",
    )
    tainted_count: int = Field(default=0, ge=0, description="Memory items newly tainted.")

    @property
    def isolated(self) -> bool:
        """Whether this order isolated the Cell (it carries the cell.isolated event)."""
        return self.event_id is not None


class LiftOutcome(BaseModel):
    """What the human's lift did: the isolation it ended, the wax it cleared, the holds released."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="The Cell lifted.")
    event_id: EventIdField = Field(description="The cell.isolation_lifted event.")
    isolated_event_id: EventIdField | None = Field(
        default=None, description="The cell.isolated event it ended; None when only holds lifted."
    )
    wax_cleared: str | None = Field(default=None, description="The BLOCK Cell Wax it cleared.")
    egress: EgressOutcome = Field(
        default=EgressOutcome.UNTRACKED, description="What happened to the Cell's egress."
    )
    released_holds: int = Field(default=0, ge=0, description="Placement holds released.")
