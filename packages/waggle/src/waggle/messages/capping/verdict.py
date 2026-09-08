"""Define the capping family's word after the checks: the verdict, each postcondition, a rollback.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The Capping
gate is the quality gate every action with a side effect outside a lease's scratch directory passes:
propose, check, apply, verify, roll back. This module is what the gate says once the checks have
run. ``Verdict`` is its final word on a proposal (a ``VerdictOutcome``: verified, rejected, rolled
back or changes requested, with what failed); ``PostconditionResult`` reports one postcondition, or
one of a task's acceptance criteria, checked after apply by someone other than the proposer;
``RollbackDone`` reports that an applied proposal was rolled back, by which ``RollbackMethod``,
whether completely, and what residue remains. The gate's entry (``ProposalSubmitted``,
``CheckResult``) lives in ``waggle.messages.capping.proposals``, from which this module takes
``CheckKind``; the split is by responsibility so each file stays under the codingrules 5.1 size
limit. Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by Wardens (the always-on supervisors of one Cell each, which
    run the gate) and read by Workers and the Queen (the central orchestrator); calls into
    waggle.messages.base, waggle.messages.labels and waggle.messages.capping.proposals only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A postcondition index always fits the proposal's list (0 to MAX_POSTCONDITIONS - 1), so a
      Verdict or a PostconditionResult can never point past it.

See Also:
    - docs/waggle/spec.md section 8.9 for the normative fields, bounds and validators.
    - waggle.messages.capping.proposals for ProposalSubmitted, CheckResult and CheckKind.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    CellIdField,
    MessageIdField,
    TaskIdField,
    WaggleMessage,
)
from waggle.messages.capping.proposals import MAX_POSTCONDITIONS, CheckKind
from waggle.messages.labels import HoneyClearance, PostconditionKind

MIN_POSTCONDITION_INDEX = 0  # The first postcondition or acceptance criterion.
MAX_POSTCONDITION_INDEX = MAX_POSTCONDITIONS - 1  # 31: the last slot a proposal's list can hold.
MAX_OBSERVED_CHARS = 4_000  # What the check found: a status, a text, a test name; never a log.
MAX_SNAPSHOT_ID_CHARS = 128  # A backend's snapshot handle (a ZFS name, a VM checkpoint id).
MAX_RESIDUAL_PATHS = 64  # As many as an action touches (MAX_PATHS): nothing more can be left.

__all__ = [
    "MAX_OBSERVED_CHARS",
    "MAX_POSTCONDITION_INDEX",
    "MAX_RESIDUAL_PATHS",
    "MAX_SNAPSHOT_ID_CHARS",
    "MIN_POSTCONDITION_INDEX",
    "PostconditionResult",
    "RollbackDone",
    "RollbackMethod",
    "Verdict",
    "VerdictOutcome",
]


class VerdictOutcome(Enum):
    """The terminal state a proposal reached at the gate."""

    VERIFIED = "VERIFIED"  # Applied, and every postcondition held.
    REJECTED = "REJECTED"  # A check failed before apply.
    ROLLED_BACK = "ROLLED_BACK"  # Applied, a postcondition failed, and the apply was undone.
    CHANGES_REQUESTED = "CHANGES_REQUESTED"  # A judge or human asked for a revised proposal.


class RollbackMethod(Enum):
    """How an applied proposal was undone."""

    SNAPSHOT = "SNAPSHOT"  # The Cell was restored from a snapshot taken before apply.
    REVERSE_DIFF = "REVERSE_DIFF"  # The diff was applied in reverse.
    NONE = "NONE"  # Nothing possible: the documented no-op on Real Cells.


# The outcomes a check rung, rather than a failed postcondition, decides.
_CHECK_DECIDED = frozenset({VerdictOutcome.REJECTED, VerdictOutcome.CHANGES_REQUESTED})

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# An index into a proposal's postconditions or a task's acceptance list.
_PostconditionIndex = Annotated[int, Field(ge=MIN_POSTCONDITION_INDEX, le=MAX_POSTCONDITION_INDEX)]


class Verdict(WaggleMessage):
    """Give the gate's final word on a proposal (capping.verdict, a reply).

    From the Warden to the proposing Worker: verified (applied and postconditions held),
    rejected, rolled back, or changes requested, with the reason and what failed.
    """

    proposal_id: MessageIdField = Field(description="The proposal.")
    task_id: TaskIdField = Field(description="The task.")
    cell_id: CellIdField = Field(description="The Cell the proposal ran on.")
    outcome: VerdictOutcome = Field(description="The terminal state the proposal reached.")
    reason: _Reason = Field(description="Why, in one line.")
    failing_check: CheckKind | None = Field(
        description="The rung that rejected or requested changes; set exactly when outcome is "
        "REJECTED or CHANGES_REQUESTED.",
    )
    failing_postcondition: _PostconditionIndex | None = Field(
        description="Index of the first postcondition that did not hold; set exactly when "
        "outcome is ROLLED_BACK.",
    )

    @model_validator(mode="after")
    def _failure_fields_match_outcome(self) -> Verdict:
        """Require the failing check or postcondition its outcome implies, and refuse the others."""
        # A rejection names the rung that decided it and a rollback the postcondition that
        # failed; a verified verdict names neither. Anything else is a contradiction the trail
        # would record as two different stories. Both fields are checked in both directions.
        if (self.outcome in _CHECK_DECIDED) != (self.failing_check is not None):
            raise ValueError(
                f"Verdict failing_check is set exactly when outcome is REJECTED or "
                f"CHANGES_REQUESTED, got outcome {self.outcome.value} with failing_check "
                f"{self.failing_check}."
            )
        if (self.outcome is VerdictOutcome.ROLLED_BACK) != (self.failing_postcondition is not None):
            raise ValueError(
                f"Verdict failing_postcondition is set exactly when outcome is ROLLED_BACK, got "
                f"outcome {self.outcome.value} with failing_postcondition "
                f"{self.failing_postcondition}."
            )
        return self


class PostconditionResult(WaggleMessage):
    """Report one postcondition or acceptance check after apply (capping.postcondition_result).

    An event from the Warden to the Queen and to the Worker, by someone other than the proposer:
    did it hold, and what was observed. With proposal_id None this is the acceptance-check
    result for task.assign.
    """

    proposal_id: MessageIdField | None = Field(
        description="The proposal; None for an acceptance criterion."
    )
    task_id: TaskIdField = Field(description="The task whose proposal or acceptance is checked.")
    cell_id: CellIdField = Field(description="The Cell the check ran on.")
    index: _PostconditionIndex = Field(
        description="Position in the proposal's postconditions or the task's acceptance list."
    )
    kind: PostconditionKind = Field(description="Repeated so the trail row stands alone.")
    has_held: bool = Field(description="Whether the assertion held.")
    observed: str = Field(
        default="", max_length=MAX_OBSERVED_CHARS, description="What was actually found."
    )
    clearance: HoneyClearance = Field(description="The label of observed.")
    reason: _Reason = Field(
        description="The checker's reason; on failure, the failing assertion attached to the "
        "Alarm.",
    )


class RollbackDone(WaggleMessage):
    """Report that an applied proposal was rolled back (capping.rollback_done, an event).

    From the Warden to the Queen and to the Worker, after a postcondition failed: by which
    method, whether completely, and what residue remains.
    """

    proposal_id: MessageIdField = Field(description="The proposal.")
    task_id: TaskIdField = Field(description="The task.")
    cell_id: CellIdField = Field(description="The Cell rolled back.")
    method: RollbackMethod = Field(description="How.")
    snapshot_id: Annotated[str, Field(max_length=MAX_SNAPSHOT_ID_CHARS)] | None = Field(
        description="The snapshot restored; set exactly when method is SNAPSHOT."
    )
    is_complete: bool = Field(description="True when the Cell is back to its pre-apply state.")
    residual_paths: tuple[Annotated[str, Field(max_length=MAX_PATH_CHARS)], ...] = Field(
        max_length=MAX_RESIDUAL_PATHS,
        description="Paths that could not be restored; empty exactly when is_complete.",
    )
    reason: _Reason = Field(description="Why the rollback happened and, if incomplete, why.")

    @model_validator(mode="after")
    def _snapshot_and_residue_match(self) -> RollbackDone:
        """Require a snapshot id for SNAPSHOT only, and residual paths when incomplete only."""
        # A snapshot restore without its handle cannot be audited and any other method has no
        # snapshot to name; a complete rollback with residue, or an incomplete one without, is a
        # contradiction. Both fields are checked in both directions.
        if (self.method is RollbackMethod.SNAPSHOT) != (self.snapshot_id is not None):
            raise ValueError(
                f"RollbackDone snapshot_id is set exactly when method is SNAPSHOT, got method "
                f"{self.method.value} with snapshot_id {self.snapshot_id}."
            )
        if self.is_complete == bool(self.residual_paths):
            raise ValueError(
                f"RollbackDone residual_paths is empty exactly when is_complete, got "
                f"is_complete {self.is_complete} with {len(self.residual_paths)} path(s)."
            )
        return self
