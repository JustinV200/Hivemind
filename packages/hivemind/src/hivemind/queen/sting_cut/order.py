"""Define StingCutOrder and what cutting one Cell off reports back (roadmap step 13.4a).

A Sting Cut is the emergency disconnect of one Cell (a unit of compute: a Virtual Cell the Hive
provisioned, or a Real Cell it borrows). A `StingCutOrder` is one as data: the Cell, who ordered it
(the human, for any Cell, or the Queen, for a dire pattern, after an isolation and never on the
Hive Stand), why, the Guard report it answers and the trail ids behind it. The one cut path
(`hivemind.queen.sting_cut.path`) answers with a `StingCutOutcome`: cut (with what each step did),
found already cut, or refused and why. `ScrubResult` says how far the Cell's own Warden confirmed
the lease's release: its processes killed and its scratch removed.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Built by the Queen's Guard request decision and her Sting Cut door (the
    human's); read by the path and the Hive Entrance's replies. Calls into `hivemind.guard`
    (GuardReportId) and pydantic/waggle only.

Key invariants:
    - Every model is frozen and forbids extras; every string is bounded; ids only, never content.
    - An outcome is `cut` exactly when it carries the `cell.sting_cut` event id.

See Also:
    - .claude/roadmap.md step 13.4a.
    - hivemind.queen.sting_cut.path for the one code path.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.guard import GuardReportId
from waggle.messages.base import CellIdField, DeviceIdField, EventIdField, GrantIdField

MAX_STING_CUT_REASON_CHARS = 300  # A sentence naming ids, like every trail reason in the Hive.
MAX_CUT_EVIDENCE_EVENTS = 66  # A report's 64 cited events, plus the Alarm and the decision.

__all__ = [
    "MAX_CUT_EVIDENCE_EVENTS",
    "MAX_STING_CUT_REASON_CHARS",
    "Cutter",
    "ScrubResult",
    "StingCutOrder",
    "StingCutOutcome",
    "StingCutRefusal",
]

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class Cutter(Enum):
    """Who ordered a Sting Cut."""

    QUEEN = "queen"  # Her rule on a dire pattern, or her awake decision, after an isolation.
    HUMAN = "human"  # The human's own lever at the Hive Entrance: any Cell, the Hive Stand's too.


class StingCutRefusal(Enum):
    """Why a Sting Cut did not happen."""

    HIVE_STAND = "hive_stand"  # The Queen may never cut the Hive Stand's own lease.
    NOT_ISOLATED = "not_isolated"  # The Queen cuts only a Cell an isolation already stands on.
    HOURLY_CAP = "hourly_cap"  # The Queen reached max_autonomous_sting_cuts_per_hour.
    NO_EVIDENCE = "no_evidence"  # The Queen could not keep the Cell's evidence before scrubbing.
    NOT_HELD = "not_held"  # The orderer does not hold `sting_cut`.


class ScrubResult(Enum):
    """How far the Cell's own Warden confirmed its lease's release."""

    CONFIRMED = "confirmed"  # It answered: processes killed and scratch removed.
    ORDERED = "ordered"  # The order went out; its answer is the Warden's to send (a Real Cell).
    UNCONFIRMED = "unconfirmed"  # It never answered in time, or said scratch was left behind.
    UNREACHED = "unreached"  # No Warden link to order it through: nothing ran there to stop.


class StingCutOrder(BaseModel):
    """One Sting Cut to carry out: the Cell, who ordered it, why, and on what evidence."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="The Cell to cut off.")
    cut_by: Cutter = Field(description="The Queen, or the human.")
    reason: str = Field(
        min_length=1, max_length=MAX_STING_CUT_REASON_CHARS, description="Why, naming ids only."
    )
    report_id: GuardReportId | None = Field(
        default=None, description="The Guard report the cut answers, if any."
    )
    evidence: tuple[EventIdField, ...] = Field(
        default=(),
        max_length=MAX_CUT_EVIDENCE_EVENTS,
        description="The trail ids that justified it, oldest first.",
    )
    decision_event_id: EventIdField | None = Field(
        default=None, description="The queen.decided event that decided it, for her cuts."
    )
    device_id: DeviceIdField | None = Field(
        default=None, description="The enrolled device the human ordered it from, for theirs."
    )


class StingCutOutcome(BaseModel):
    """What one Sting Cut did: cut the Cell off, found it cut already, or was refused."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="The Cell named.")
    event_id: EventIdField | None = Field(
        default=None, description="The cell.sting_cut event; None when nothing was cut now."
    )
    already_cut: bool = Field(
        default=False, description="The Cell was cut before this order; nothing changed."
    )
    refusal: StingCutRefusal | None = Field(
        default=None, description="Why the cut was refused, when it was."
    )
    isolated_event_id: EventIdField | None = Field(
        default=None, description="The cell.isolated event the cut follows, when one stands."
    )
    snapshot_id: str | None = Field(
        default=None, max_length=128, description="The snapshot kept as evidence, if any."
    )
    revoked_grant_ids: tuple[GrantIdField, ...] = Field(
        default=(), description="The grants on the Cell it revoked."
    )
    scrub: ScrubResult = Field(
        default=ScrubResult.UNREACHED, description="How far the lease's release was confirmed."
    )
    key_forgotten: bool = Field(
        default=False, description="The Cell's key is forgotten: its Warden can never redial."
    )
    torn_down: bool = Field(
        default=False, description="A Night Veil Cell torn down, its retained records purged."
    )

    @property
    def cut(self) -> bool:
        """Whether this order cut the Cell off (it carries the cell.sting_cut event)."""
        return self.event_id is not None
