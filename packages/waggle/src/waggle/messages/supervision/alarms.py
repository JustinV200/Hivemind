"""Define the supervision family's Alarms: raise one up the tree and close it at every level.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and its
supervision family is the one ``Supervisor`` protocol at every level of the tree (human, Queen,
Wardens, sub-bees). An Alarm is an issue a bee cannot resolve, escalated to its supervisor:
``AlarmRaised`` carries it up, keeping the same alarm id, origin and attempt count at every level
so no level handles it twice, and the human is always last, reached only through the Queen (the
central orchestrator); ``AlarmResolved`` closes it at every level that saw it, saying how and
why, so no level keeps re-escalating it. ``AlarmKind`` is the closed set the escalation policy
keys on, ``AlarmResolution`` how an Alarm was settled, and ``AlarmContext`` the typed references
(task, Cell, bee, trail event, Handoff) to what it is about; a Cell is a unit of compute, a
Warden the always-on supervisor of one, a Worker a sub-bee spawned for one task, and a Handoff
the document a bee writes before its context is reset. The family's other messages are split
out by responsibility so each file stays under the codingrules 5.1 size limit. Every bound is a
named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built and read by every Worker, every Warden and the Queen;
    mirrored by hivemind.supervision (Alarm, AlarmKind); calls into waggle.messages.base and
    waggle.messages.labels only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - An Alarm's detail is bounded, so an escalation can never carry a transcript up the tree.

See Also:
    - docs/waggle/spec.md section 8.3 for the normative fields, bounds and validators.
    - waggle.messages.supervision.oversight for Heartbeat, Inspect, InspectReply and Intervene,
      whose alarm_id links an intervention back to the Alarm it answers.
    - waggle.messages.labels for AlarmSeverity, HandoffRef and HoneyClearance.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field

from waggle.ids import HiveId, IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MAX_REASON_CHARS,
    VALUE_MODEL_CONFIG,
    AlarmIdField,
    CellIdField,
    EventIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
    WorkerIdField,
    id_validator,
)
from waggle.messages.labels import AlarmSeverity, HandoffRef, HoneyClearance

MIN_ATTEMPTS = 0  # The raising hop has tried nothing yet; each level above adds one.
MIN_DETAIL_CHARS = 1  # An Alarm always says what went wrong.
MAX_DETAIL_CHARS = 2_000  # A failing assertion, an error sentence or an observation; no transcript.

__all__ = [
    "MAX_DETAIL_CHARS",
    "MIN_ATTEMPTS",
    "MIN_DETAIL_CHARS",
    "AlarmContext",
    "AlarmKind",
    "AlarmRaised",
    "AlarmResolution",
    "AlarmResolved",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums and value models
# ──────────────────────────────────────────────────────────────────────────────


class AlarmKind(Enum):
    """What went wrong, as the escalation policy keys it; OTHER holds anything not yet named."""

    WORKER_FAILED = "WORKER_FAILED"
    WORKER_CRASHED = "WORKER_CRASHED"
    WORKER_STALLED = "WORKER_STALLED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    GRANT_EXCEEDED = "GRANT_EXCEEDED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AUDIT_FAILED = "AUDIT_FAILED"
    CELL_UNREACHABLE = "CELL_UNREACHABLE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"  # A lease's scratch directory outgrew its configured quota.
    OTHER = "OTHER"  # Anything new, until a minor bump names it.


class AlarmResolution(Enum):
    """How an Alarm was settled, so the trail and every level that saw it agree on the outcome."""

    RETRIED = "RETRIED"
    RESPAWNED = "RESPAWNED"
    REBOUND = "REBOUND"  # The bee was moved to another model slot.
    TAKEN_OVER = "TAKEN_OVER"  # The supervisor resumed the task itself.
    CANCELLED = "CANCELLED"
    HUMAN = "HUMAN"  # The human decided; the Queen reports it on their behalf.
    SELF_CLEARED = "SELF_CLEARED"  # The condition went away before anyone acted.


class AlarmContext(BaseModel):
    """Typed references to what an Alarm is about; every one optional, none a transcript."""

    model_config = VALUE_MODEL_CONFIG

    task_id: TaskIdField | None = Field(description="The task concerned, if any.")
    cell_id: CellIdField | None = Field(description="The Cell concerned, if any.")
    worker_id: WorkerIdField | None = Field(
        description="The bee concerned, which may differ from origin when a Warden raises about "
        "a sub-bee."
    )
    event_id: EventIdField | None = Field(
        description="The trail event that best explains it, if any."
    )
    handoff: HandoffRef | None = Field(
        description="The stuck bee's last Handoff, so a takeover can resume from it."
    )


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A bee that can raise an Alarm: a Worker or a Warden, never the Queen or a device.
_BeeId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]
# A supervisor that can resolve one: a Warden or the Queen (a hive_ id), never a Worker.
_SupervisorId = Annotated[WardenId | HiveId, id_validator(IdKind.WARDEN, IdKind.HIVE)]


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class AlarmRaised(WaggleMessage):
    """Escalate an issue the sender cannot resolve (supervision.alarm_raised, an event).

    The same alarm id, origin and attempt count travel at every level; the human is always last
    and only the Queen reaches them.
    """

    alarm_id: AlarmIdField = Field(
        description="Identical at every level so no level handles it twice."
    )
    kind: AlarmKind = Field(description="What went wrong, as the policy table keys it.")
    severity: AlarmSeverity = Field(description="How bad.")
    origin: _BeeId = Field(description="The bee that first raised it; survives forwarding.")
    attempts: int = Field(
        ge=MIN_ATTEMPTS,
        description="Resolution attempts made so far across levels: 0 on the raising hop, "
        "incremented once per level, which each receiver checks against its own count.",
    )
    raised_at: UtcDatetime = Field(
        description="When it was first raised; its age feeds the Attendant's score (the "
        "Attendant is a supervisor's inbox triage)."
    )
    context: AlarmContext = Field(
        description="Typed references to the task, Cell, bee, trail event and Handoff."
    )
    detail: str = Field(
        min_length=MIN_DETAIL_CHARS,
        max_length=MAX_DETAIL_CHARS,
        description="The failing assertion, error sentence or observation; never a transcript.",
    )
    clearance: HoneyClearance = Field(description="The label of detail.")
    reason: _Reason = Field(description="Why the sender escalates instead of handling it.")


class AlarmResolved(WaggleMessage):
    """Close an Alarm at every level that saw it (supervision.alarm_resolved, an event).

    Says how and why it was resolved, so no level keeps re-escalating it.
    """

    alarm_id: AlarmIdField = Field(description="The Alarm closed.")
    resolution: AlarmResolution = Field(description="How it was resolved.")
    resolved_by: _SupervisorId = Field(
        description="The supervisor that resolved it (the Queen when the human did)."
    )
    reason: _Reason = Field(description="Why this resolution.")
