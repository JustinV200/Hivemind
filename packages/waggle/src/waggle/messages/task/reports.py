"""Define the task family's upward messages: progress on a task and the result that closes it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the task
family carries the lifecycle of one task: assigned by the Queen (the central orchestrator) to the
Warden (the always-on supervisor of one Cell, a unit of compute) of the placed Cell, run by a Worker
(a sub-bee spawned for one task), reported on, and closed. The two messages here travel back up the
tree, Worker to Warden and Warden to Queen, and correlate to the ``TaskAssign`` they answer:
``TaskProgress`` reports a stage change, carrying the Handoff (the document a bee writes before its
context is reset) when the stage is a checkpoint, and ``TaskResult`` closes an attempt with its
outcome, artifacts and spend, on two hops: an unverified claim from the Worker, then the Warden's
verified result after the acceptance checks. Neither ever carries a transcript; a summary is a
paragraph. The downward half of the family (``TaskAssign`` and the orders on a task in flight) lives
in ``waggle.messages.task.assignment``, from which the attempt bound is imported so both halves
count attempts alike. Every other bound is a named constant here; the number, not the name, is
normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device connector),
    inside the waggle package. Registered by waggle.messages.registry, which maps each class to its
    kind; built by every Worker and Warden and read by every Warden and the Queen; calls into
    waggle.messages.base, waggle.messages.labels and waggle.messages.task.assignment only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A TaskResult names the Warden that checked it exactly when its outcome is terminal, so a
      Worker's claim can never read as a verified result (validator).

See Also:
    - docs/waggle/spec.md section 8.2 for the normative fields, bounds and validators.
    - waggle.messages.task.assignment for TaskAssign, whose attempt every report echoes, and the
      orders.
    - waggle.messages.labels for HandoffRef and HoneyClearance.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import (
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    VALUE_MODEL_CONFIG,
    TaskIdField,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.labels import HandoffRef, HoneyClearance
from waggle.messages.task.assignment import MIN_ATTEMPT

MIN_SUMMARY_CHARS = 1  # A report always says something; an empty summary reports nothing.
MAX_SUMMARY_CHARS = 2_000  # One paragraph of what changed or what was done; never a transcript.
MIN_FRACTION_DONE = 0.0  # An estimate of completion is a share of the work, so it starts at 0.
MAX_FRACTION_DONE = 1.0  # And ends at 1: the task is done, not more than done.
MAX_ARTIFACTS = 64  # Outputs travel by reference; more than this belongs in a manifest file.
MIN_ARTIFACT_SIZE_BYTES = 0  # A file is never smaller than empty.
MIN_SPEND = 0.0  # Spend is never negative; the Hive meters cost, never refunds.

__all__ = [
    "MAX_ARTIFACTS",
    "MAX_FRACTION_DONE",
    "MAX_SUMMARY_CHARS",
    "MIN_ARTIFACT_SIZE_BYTES",
    "MIN_FRACTION_DONE",
    "MIN_SPEND",
    "MIN_SUMMARY_CHARS",
    "ArtifactRef",
    "TaskOutcome",
    "TaskProgress",
    "TaskResult",
    "TaskStage",
]


class TaskStage(Enum):
    """What a TaskProgress reports; STARTED is the Warden's evidence for ASSIGNED -> RUNNING."""

    STARTED = "STARTED"
    WORKING = "WORKING"
    CHECKPOINTED = "CHECKPOINTED"  # A Handoff was just written; the report carries it.
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"


class TaskOutcome(Enum):
    """How an attempt ended; the Worker may only claim, the Warden's hop is a terminal state."""

    CLAIMED = "CLAIMED"  # The Worker's hop: work done, acceptance not yet run.
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class ArtifactRef(BaseModel):
    """An output the task produced, by reference; contents never travel."""

    model_config = VALUE_MODEL_CONFIG

    path: str = Field(
        max_length=MAX_PATH_CHARS, description="The scratch or capped-and-applied path."
    )
    size_bytes: int = Field(ge=MIN_ARTIFACT_SIZE_BYTES, description="Size of the file.")
    sha256: str = Field(pattern=SHA256_PATTERN, description="Digest of the file, lowercase hex.")


class TaskProgress(WaggleMessage):
    """Report a stage change or periodic progress on an assigned task (task.progress, an event).

    Carries the Handoff reference when the stage is a checkpoint.
    """

    task_id: TaskIdField = Field(description="The task reported on.")
    attempt: int = Field(
        ge=MIN_ATTEMPT,
        description="Echo of TaskAssign.attempt; a report from a superseded attempt is ignored.",
    )
    stage: TaskStage = Field(description="What happened.")
    summary: str = Field(
        min_length=MIN_SUMMARY_CHARS,
        max_length=MAX_SUMMARY_CHARS,
        description="One paragraph of what changed since the last report; never a transcript.",
    )
    clearance: HoneyClearance = Field(description="The label of summary.")
    fraction_done: Annotated[float, Field(ge=MIN_FRACTION_DONE, le=MAX_FRACTION_DONE)] | None = (
        Field(description="Estimated completion; None when the bee cannot estimate.")
    )
    handoff: HandoffRef | None = Field(
        description="The Handoff just written. Required when stage is CHECKPOINTED, None otherwise."
    )

    @model_validator(mode="after")
    def _handoff_matches_stage(self) -> TaskProgress:
        """Require a Handoff on a checkpoint and forbid one on any other stage."""
        # A checkpoint without its Handoff leaves nothing to resume from; a Handoff on a WORKING
        # report would be a checkpoint the Queen never learns of. Both directions are checked.
        checkpointed = self.stage is TaskStage.CHECKPOINTED
        if checkpointed != (self.handoff is not None):
            raise ValueError(
                f"TaskProgress handoff is required exactly when stage is CHECKPOINTED, got "
                f"stage {self.stage.value} with handoff {self.handoff}."
            )
        return self


class TaskResult(WaggleMessage):
    """Close an attempt with its outcome, artifacts, spend and reason (task.result, an event).

    Two hops: an unverified claim from the Worker, then the Warden's verified result after the
    acceptance checks. The Warden rejects a task.result from a Worker sender whose outcome is
    not CLAIMED (control.error, hive.task.self_verified) and the Queen rejects one whose
    checked_by is not the envelope sender; both are receiver rules, not validators here.
    """

    task_id: TaskIdField = Field(description="The task concluded.")
    attempt: int = Field(ge=MIN_ATTEMPT, description="Echo of TaskAssign.attempt.")
    outcome: TaskOutcome = Field(
        description="CLAIMED on the Worker's hop, a terminal state on the Warden's; checked_by "
        "is set exactly when it is not CLAIMED."
    )
    summary: str = Field(
        min_length=MIN_SUMMARY_CHARS,
        max_length=MAX_SUMMARY_CHARS,
        description="What was done, in prose; never a transcript.",
    )
    clearance: HoneyClearance = Field(description="The label of summary and of the artifact paths.")
    artifacts: tuple[ArtifactRef, ...] = Field(
        max_length=MAX_ARTIFACTS,
        description="Outputs produced, by path, size and digest; may be empty.",
    )
    checked_by: WardenIdField | None = Field(
        description="The Warden that ran the acceptance checks; None on the hop from the "
        "Worker, which may only claim. Per-criterion results travel as "
        "capping.postcondition_result events with no proposal id."
    )
    handoff: HandoffRef | None = Field(
        description="The bee's last Handoff, so a failed or cancelled task can be retried from it."
    )
    spend: float = Field(ge=MIN_SPEND, description="The attempt's total spend.")
    reason: _Reason = Field(
        description="Why this outcome: the failure cause, the cancellation cause, or the "
        "acceptance summary."
    )

    @model_validator(mode="after")
    def _checked_by_matches_outcome(self) -> TaskResult:
        """Require checked_by on a terminal outcome and forbid it on a claim."""
        # A terminal outcome with no checker would let a Worker's self-verdict pass as the
        # Warden's; a claim with a checker would name checks that never ran. Both are refused.
        claimed = self.outcome is TaskOutcome.CLAIMED
        if claimed == (self.checked_by is not None):
            raise ValueError(
                f"TaskResult checked_by is set exactly when outcome is not CLAIMED, got outcome "
                f"{self.outcome.value} with checked_by {self.checked_by}."
            )
        return self
