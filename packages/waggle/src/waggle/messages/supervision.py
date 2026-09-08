"""Define the supervision family's liveness and levers: heartbeat, inspect, its reply, intervene.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and its
supervision family is the one ``Supervisor`` protocol at every level of the tree (human, Queen,
Wardens, sub-bees): heartbeats with context telemetry, Alarms escalated upward, inspection,
intervention and questions that block a task until answered. Full transcripts never travel up
the tree. The four messages here are the liveness beat and the supervisor's direct levers:
``Heartbeat`` reports the sender's state and telemetry (a Warden, the always-on supervisor of
one Cell, a unit of compute, adds one row per sub-bee and renews its Forage grant; Forage is
capacity as data), ``Inspect`` asks for a compacted view of a bee's context under a size cap and
``InspectReply`` returns it, and ``Intervene`` pulls one lever (compact, checkpoint, handoff,
rebind, takeover, cancel) on a child or one of its sub-bees. The Queen (the central orchestrator)
never addresses a Worker (a sub-bee spawned for one task) directly, so reaching one goes through
its Warden. The rest of the family is split out by responsibility so each file stays under the
codingrules 5.1 size limit: the value models in ``waggle.messages.supervision_telemetry``,
Alarms in ``waggle.messages.supervision_alarms`` and blocking questions in
``waggle.messages.supervision_questions``. Every bound is a named constant here; the number, not
the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built and read by the Queen, every Warden and every Worker; calls
    into waggle.messages.base, waggle.messages.labels and waggle.messages.supervision_telemetry
    only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A Heartbeat says which kind of bee sent it: exactly one of worker_state and warden_state
      is set (validator).

See Also:
    - docs/waggle/spec.md section 8.3 for the normative fields, bounds and validators.
    - waggle.messages.supervision_telemetry for ContextTelemetry, ChildTelemetry, CompactView
      and the two state enums.
    - waggle.messages.supervision_alarms and waggle.messages.supervision_questions for the
      family's other four messages.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MAX_REASON_CHARS,
    MAX_SLOT_CHARS,
    MAX_SUB_BEES_ON_WIRE,
    SLOT_PATTERN,
    AlarmIdField,
    GrantIdField,
    TaskIdField,
    WaggleMessage,
    WorkerIdField,
    id_validator,
)
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision_telemetry import (
    MAX_VIEW_CHARS,
    MIN_SPEND,
    ChildTelemetry,
    CompactView,
    ContextTelemetry,
    WardenState,
    WorkerState,
)

MIN_INTERVAL_S = 0.0  # Exclusive: an interval of 0 would size the receiver's watchdog to nothing.
MIN_INSPECT_CHARS = 256  # The least a view can say something in; a smaller cap returns noise.

__all__ = [
    "MIN_INSPECT_CHARS",
    "MIN_INTERVAL_S",
    "Heartbeat",
    "Inspect",
    "InspectReply",
    "Intervene",
    "InterventionAction",
]


class InterventionAction(Enum):
    """The supervisor's levers; a Warden holds them all minus takeover with the Queen's slot."""

    COMPACT = "COMPACT"  # Compact the bee's context in place.
    CHECKPOINT = "CHECKPOINT"  # Write a Handoff and carry on.
    HANDOFF = "HANDOFF"  # Write a Handoff and stop; a fresh bee resumes from it.
    REBIND = "REBIND"  # Move the bee to another model slot; slot is required.
    TAKEOVER = "TAKEOVER"  # The supervisor resumes the task itself from the bee's Handoff.
    CANCEL = "CANCEL"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A model slot (the named role a model is bound to) as the conventions fix it.
_Slot = Annotated[str, Field(max_length=MAX_SLOT_CHARS, pattern=SLOT_PATTERN)]
# A bee that can be described: a Worker or a Warden, never the Queen or a device.
_BeeId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


class Heartbeat(WaggleMessage):
    """Report liveness, state and telemetry, and renew a grant (supervision.heartbeat, an event).

    A Worker reports its own telemetry; a Warden adds one row per sub-bee and the grant it
    renews. The receiver's watchdog sizes its timeout from interval_s.
    """

    telemetry: ContextTelemetry = Field(description="The sender's own telemetry.")
    task_id: TaskIdField | None = Field(
        description="The sender's current task; None while idle or in WATCH."
    )
    worker_state: WorkerState | None = Field(
        description="The sender's state when it is a Worker; None when it is a Warden."
    )
    warden_state: WardenState | None = Field(
        description="The sender's state when it is a Warden; None when it is a Worker. Exactly "
        "one of worker_state and warden_state is set."
    )
    children: tuple[ChildTelemetry, ...] = Field(
        max_length=MAX_SUB_BEES_ON_WIRE,
        description="A Warden's per-sub-bee rows, worker ids unique; empty for a Worker.",
    )
    grant_id: GrantIdField | None = Field(
        description="The grant this heartbeat renews; None for a Worker or a Warden without a "
        "shared grant."
    )
    grant_spend: Annotated[float, Field(ge=MIN_SPEND)] | None = Field(
        description="Total spend charged to that grant so far; requires grant_id."
    )
    interval_s: float = Field(
        gt=MIN_INTERVAL_S,
        description="The sender's configured heartbeat interval, so the receiver's watchdog can "
        "size its timeout.",
    )

    @field_validator("children")
    @classmethod
    def _children_unique(cls, value: tuple[ChildTelemetry, ...]) -> tuple[ChildTelemetry, ...]:
        """Reject two rows for the same sub-bee."""
        # Two rows for one Worker would make the Queen count it twice against the sub-bee cap
        # and read whichever state came last; a duplicate is a sender bug, so it is refused.
        if len({child.worker_id for child in value}) != len(value):
            raise ValueError("Heartbeat children must name each worker id once.")
        return value

    @model_validator(mode="after")
    def _exactly_one_state(self) -> Heartbeat:
        """Require exactly one of worker_state and warden_state."""
        # The state says which kind of bee sent the beat; none means an unknown bee and both
        # means a contradiction, so each is refused.
        if (self.worker_state is None) == (self.warden_state is None):
            raise ValueError(
                f"Heartbeat sets exactly one of worker_state and warden_state, got "
                f"worker_state {self.worker_state} and warden_state {self.warden_state}."
            )
        return self

    @model_validator(mode="after")
    def _grant_spend_requires_grant(self) -> Heartbeat:
        """Reject a spend figure with no grant to charge it to."""
        # A spend with no grant id cannot be booked anywhere, so the Queen could only drop it.
        if self.grant_spend is not None and self.grant_id is None:
            raise ValueError(
                f"Heartbeat grant_spend {self.grant_spend} requires a grant_id to charge it to."
            )
        return self


class Inspect(WaggleMessage):
    """Ask a child, or one of its sub-bees, for a compacted view (supervision.inspect, a request).

    Under a size cap; the Queen never addresses a Worker directly, so reaching one goes through
    its Warden.
    """

    subject: WorkerIdField | None = Field(
        description="A sub-bee of the recipient to inspect; None means the recipient itself."
    )
    max_chars: int = Field(
        default=MAX_VIEW_CHARS,
        ge=MIN_INSPECT_CHARS,
        le=MAX_VIEW_CHARS,
        description="The cap the reply's view must honour.",
    )


class InspectReply(WaggleMessage):
    """Return the bee's telemetry and compacted view (supervision.inspect_reply, a reply)."""

    subject: _BeeId = Field(description="The bee described.")
    task_id: TaskIdField | None = Field(description="The subject's current task, if any.")
    telemetry: ContextTelemetry = Field(description="The subject's telemetry at reply time.")
    view: CompactView = Field(
        description="The compacted view; total size at most the request's max_chars, which the "
        "receiver checks against its own Inspect."
    )
    clearance: HoneyClearance = Field(
        description="The label of the view, derived from the hot state it summarises."
    )
    is_truncated: bool = Field(description="Whether the view was cut to fit the cap.")


class Intervene(WaggleMessage):
    """Pull a supervisor lever on a child or one of its sub-bees (supervision.intervene, an event).

    Compact, checkpoint, handoff, rebind to a slot, takeover or cancel. Wardens hold the same
    levers over their sub-bees minus takeover with the Queen's slot.
    """

    action: InterventionAction = Field(description="The lever pulled.")
    subject: WorkerIdField | None = Field(
        description="The recipient's sub-bee the action targets; None means the recipient itself."
    )
    task_id: TaskIdField | None = Field(
        description="The task concerned when the subject holds more than one."
    )
    slot: _Slot | None = Field(
        description="The model slot to rebind to. Required when action is REBIND, None otherwise."
    )
    alarm_id: AlarmIdField | None = Field(
        description="The Alarm this intervention answers, so the trail links the two."
    )
    reason: _Reason = Field(description="Why the supervisor intervenes.")

    @model_validator(mode="after")
    def _slot_matches_action(self) -> Intervene:
        """Require a slot for REBIND and forbid one for every other lever."""
        # A rebind with no slot has nowhere to go; a slot on a cancel would be read by nobody
        # and hints the sender meant a rebind. Both directions are checked.
        rebinding = self.action is InterventionAction.REBIND
        if rebinding != (self.slot is not None):
            raise ValueError(
                f"Intervene slot is required exactly when action is REBIND, got action "
                f"{self.action.value} with slot {self.slot}."
            )
        return self
