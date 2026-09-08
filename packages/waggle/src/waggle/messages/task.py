"""Define the task family's downward messages: assign a task, then cancel, pause or resume it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
task family carries the lifecycle of one task: assigned by the Queen (the central orchestrator)
to the Warden (the always-on supervisor of one Cell, a unit of compute) of the Cell placement
chose, run by a Worker (a sub-bee spawned for one task), reported on, and closed. The four
messages here travel down the tree, Queen to Warden and Warden to Worker. ``TaskAssign`` hands
the task over with its acceptance criteria, its Tempo (a speed-against-accuracy setting), its
clearance, its Forage grant (Forage is capacity as data: cores, memory, GPU, model seats and
spend) and an optional Handoff (the document a bee writes before its context is reset) to
resume from; ``TaskCancel``, ``TaskPause`` and ``TaskResume`` are the orders on a task in
flight. The reports that travel back up (``TaskProgress``, ``TaskResult``) live in
``waggle.messages.task_reports``, split out by responsibility so each file stays under the
codingrules 5.1 size limit. ``WorkerRole`` names the six Worker roles a Warden can spawn. Every
bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by the Queen and every Warden and read by every Warden and
    Worker; calls into waggle.messages.base and waggle.messages.labels only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A task never resumes from a Handoff labelled above its own clearance (TaskAssign).

See Also:
    - docs/waggle/spec.md section 8.2 for the normative fields, bounds and validators.
    - waggle.messages.task_reports for TaskProgress, TaskResult and the enums they report in.
    - waggle.messages.labels for Tempo, Postcondition, HandoffRef and HoneyClearance.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    MAX_SLOT_CHARS,
    SLOT_PATTERN,
    CellIdField,
    GrantIdField,
    TaskIdField,
    WaggleMessage,
)
from waggle.messages.labels import HandoffRef, HoneyClearance, Postcondition, Tempo

MIN_OBJECTIVE_CHARS = 1  # A task with no objective asks for nothing.
MAX_OBJECTIVE_CHARS = 8_000  # A planner's brief: a page or two; anything longer belongs in Honey.
MIN_ACCEPTANCE_ITEMS = 1  # Nothing reaches SUCCEEDED unchecked; a JUDGE_RUBRIC stands in at worst.
MAX_ACCEPTANCE_ITEMS = 32  # More criteria than one task should carry; split the task instead.
MAX_ACCEPTANCE_CHARS = 65_536  # 64 KiB across every criterion, so an assign always fits one frame.
MIN_ATTEMPT = 1  # The first try is attempt 1, so 0 can never pass for a real attempt.
MIN_GRACE_S = 0.0  # A grace period is never negative; exactly 0 kills at once (Sting Cut).

__all__ = [
    "MAX_ACCEPTANCE_CHARS",
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_OBJECTIVE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_ATTEMPT",
    "MIN_GRACE_S",
    "MIN_OBJECTIVE_CHARS",
    "TaskAssign",
    "TaskCancel",
    "TaskPause",
    "TaskResume",
    "WorkerRole",
]


class WorkerRole(Enum):
    """Which Worker role the Warden spawns for a task; the six roles that all implement Worker."""

    DRONE = "DRONE"
    FORAGER = "FORAGER"
    SCOUT = "SCOUT"
    GUARD_BEE = "GUARD_BEE"
    UNDERTAKER = "UNDERTAKER"
    HOUSE_BEE = "HOUSE_BEE"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A model slot (the named role a model is bound to) as the conventions fix it: a short
# UPPER_SNAKE name; the slot travels as data, and the manifest resolves it to a model.
_Slot = Annotated[str, Field(max_length=MAX_SLOT_CHARS, pattern=SLOT_PATTERN)]


class TaskAssign(WaggleMessage):
    """Hand a placed task to the Warden of its Cell, then to its Worker (task.assign, a request).

    Carries the acceptance criteria, Tempo, clearance, grant and optional Handoff to resume from;
    the Warden re-issues it to the Worker it spawns. A task reaches SUCCEEDED only after its
    Warden, never the Worker that did the work, has run the acceptance checks.
    """

    task_id: TaskIdField = Field(description="The task being assigned.")
    goal_id: TaskIdField = Field(
        description="The root of the task graph this task belongs to; equals task_id for a root "
        "goal. Spend is metered per goal."
    )
    cell_id: CellIdField = Field(
        description="The Cell placement chose; a Warden rejects an assign for a Cell it does not "
        "own (control.error)."
    )
    role: WorkerRole = Field(description="The Worker role the Warden spawns.")
    slot: _Slot = Field(
        description="The model slot the bee runs on; a rebind re-assigns with a stronger one."
    )
    objective: str = Field(
        min_length=MIN_OBJECTIVE_CHARS,
        max_length=MAX_OBJECTIVE_CHARS,
        description="What the task must achieve, as the planner wrote it.",
    )
    acceptance: tuple[Postcondition, ...] = Field(
        min_length=MIN_ACCEPTANCE_ITEMS,
        max_length=MAX_ACCEPTANCE_ITEMS,
        description="The criteria the Warden checks before the task may reach SUCCEEDED; a "
        "JUDGE_RUBRIC entry stands in where nothing is machine-checkable. Bounded in total "
        "characters across all criteria.",
    )
    tempo: Tempo = Field(description="The task's latency budget and accuracy bar.")
    clearance: HoneyClearance = Field(
        description="The highest label the task's bee may read, resume from or write."
    )
    grant_id: GrantIdField = Field(
        description="The Forage grant (Queen to Warden) or the slice the Warden carved (Warden "
        "to Worker) that the task's seats and spend are charged to."
    )
    attempt: int = Field(
        ge=MIN_ATTEMPT,
        description="1 for the first try; incremented on every retry, respawn, rebind or "
        "reassignment so late reports from an earlier attempt are recognisable. The Queen owns "
        "the number on Queen-to-Warden hops; a Warden-local respawn or rebind keeps it and "
        "reports it on the next task.progress.",
    )
    resume_from: HandoffRef | None = Field(
        description="The Handoff to resume from after a rebind, takeover, wake or reassignment; "
        "None for a fresh start. Its clearance never exceeds clearance."
    )
    reason: _Reason = Field(
        description="Why this task was placed here, on this role and slot, now."
    )

    @field_validator("acceptance")
    @classmethod
    def _acceptance_within_total_bound(
        cls, value: tuple[Postcondition, ...]
    ) -> tuple[Postcondition, ...]:
        """Reject criteria that together exceed MAX_ACCEPTANCE_CHARS."""
        # Each criterion is bounded on its own (labels.py), but 32 of them at their own maxima
        # would run to several hundred KiB, more than a frame holds; the total is what matters.
        total = sum(_criterion_chars(criterion) for criterion in value)
        if total > MAX_ACCEPTANCE_CHARS:
            raise ValueError(
                f"TaskAssign acceptance criteria total {total} characters, more than the "
                f"{MAX_ACCEPTANCE_CHARS} allowed across all of them."
            )
        return value

    @model_validator(mode="after")
    def _handoff_within_clearance(self) -> TaskAssign:
        """Reject a Handoff labelled above the task's own clearance."""
        # The bee may read nothing above `clearance`; a Handoff above it would put, say, C2
        # content into a C1 bee's context on its very first turn. Ranks are compared, never the
        # wire values (labels.py).
        if self.resume_from is not None and self.resume_from.clearance.rank > self.clearance.rank:
            raise ValueError(
                f"TaskAssign resume_from is labelled {self.resume_from.clearance.value}, above "
                f"the task's clearance {self.clearance.value}."
            )
        return self


class TaskCancel(WaggleMessage):
    """Order a task stopped, with a grace period to checkpoint (task.cancel, an event)."""

    task_id: TaskIdField = Field(description="The task to cancel.")
    grace_s: float = Field(
        ge=MIN_GRACE_S,
        description="Seconds the bee gets to checkpoint and release before it is killed; 0 kills "
        "at once (Sting Cut, the human's per-Cell emergency disconnect, and Absconding, the "
        "human-only mass teardown).",
    )
    reason: _Reason = Field(description="Why the task is cancelled.")


class TaskPause(WaggleMessage):
    """Order a running task to checkpoint and hold in place (task.pause, an event).

    Moves the task to PAUSED, as Clustering (the pause-and-preserve protocol while a model
    provider is unavailable) and the Supersedure freeze require. A pause always checkpoints; the
    structured cause rides on control.cluster.
    """

    task_id: TaskIdField = Field(description="The task to pause.")
    reason: _Reason = Field(description="Why it is paused.")


class TaskResume(WaggleMessage):
    """Resume a paused task in place or from its Handoff (task.resume, an event).

    In place while the bee is still warm, or from its Handoff on a fresh bee, optionally on a
    different slot.
    """

    task_id: TaskIdField = Field(description="The task to resume.")
    attempt: int = Field(
        ge=MIN_ATTEMPT,
        description="The attempt number the resumed work reports under: the paused attempt when "
        "resuming in place, a fresh one for a fresh bee.",
    )
    resume_from: HandoffRef | None = Field(
        description="The Handoff a fresh bee resumes from; None means the paused bee is still "
        "warm and continues in place."
    )
    slot: _Slot | None = Field(
        description="A different model slot to resume on when the original provider is still "
        "down; None keeps the current binding."
    )
    reason: _Reason = Field(description="Why it resumes now.")


def _criterion_chars(criterion: Postcondition) -> int:
    """Count the characters one acceptance criterion puts on the wire: subject, argv, expected."""
    # The three text parts are what a frame carries for a criterion; the kind is a fixed token.
    expected_chars = len(criterion.expected) if criterion.expected is not None else 0
    return (
        len(criterion.subject) + sum(len(argument) for argument in criterion.argv) + expected_chars
    )
