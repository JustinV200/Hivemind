"""Define CappingError and the ways a Capping proposal can be misused or fail on purpose.

Capping (`hivemind.supervision.capping`) is the QA gate every action with a side effect outside a
lease's scratch directory passes: propose, check, apply, verify, roll back (codingrules section
8.12). This module holds the ways a caller can misuse that gate on purpose: asking an unknown
proposal for its state, asking the proposal state machine (`state.TRANSITIONS`) for an edge it
does not have, or failing to reconstruct a file from a unified diff. Every one of these is
`CappingError` or one of its subclasses, rooted at `hivemind.supervision.errors.SupervisionError`
so a caller several layers up can catch one name and know it caught anything Capping itself raised
on purpose (codingrules section 10).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Raised
    by `hivemind.supervision.capping.state` (the transition table), `hivemind.supervision.capping.
    gate` (an unknown proposal id) and `hivemind.supervision.capping.diff` (a diff that does not
    apply cleanly). Imported by every module in this package that raises on purpose.

Key invariants:
    - Every CappingError subclass sets its own `code`; none shares a code with another.
    - InvalidProposalTransitionError and UnknownProposalError each subclass one of
      `hivemind.common.errors`' base categories (ConflictError, NotFoundError) because a forbidden
      transition and an unknown proposal id map cleanly onto one of those, exactly like
      `hivemind.cell.errors.InvalidLeaseTransitionError`; DiffApplyError does not, because a diff
      that will not apply is specific to this package's own `diff.py`, the same way
      `hivemind.cell.errors.SnapshotUnsupportedError` subclasses `CellError` directly.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.supervision.errors for SupervisionError, this module's own root.
    - hivemind.common.errors for ConflictError and NotFoundError, two of the base categories this
      module's classes descend from.
    - hivemind.supervision.capping.state for TRANSITIONS, the table InvalidProposalTransitionError
      reports a forbidden edge in.
    - hivemind.supervision.capping.diff for apply_unified_diff, which raises DiffApplyError.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConflictError, NotFoundError
from hivemind.supervision.errors import SupervisionError

__all__ = [
    "CappingError",
    "DiffApplyError",
    "InvalidProposalTransitionError",
    "UnknownProposalError",
]


class CappingError(SupervisionError):
    """Root of every error `hivemind.supervision.capping` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say (an unconfigured risk tier, for instance) may raise this directly.
    """

    code: ClassVar[str] = "hivemind.supervision.capping.error"


class UnknownProposalError(NotFoundError):
    """Raise when a `CappingGate` is asked for a proposal id it has never seen.

    Raised by `hivemind.supervision.capping.gate.CappingGate.get`.
    """

    code: ClassVar[str] = "hivemind.supervision.capping.unknown_proposal"

    def __init__(self, proposal_id: str) -> None:
        """Build the error for an unrecognised proposal id.

        Args:
            proposal_id: The id that was looked up and not found in the gate's proposal table.
        """
        super().__init__(f"No proposal with id {proposal_id!r} is known to this Capping gate.")
        self.proposal_id = proposal_id


class InvalidProposalTransitionError(ConflictError):
    """Raise when the Proposal state machine is asked for an edge its transition table lacks.

    Raised by `hivemind.supervision.capping.state.assert_transition`.
    """

    code: ClassVar[str] = "hivemind.supervision.capping.invalid_proposal_transition"

    def __init__(self, from_state: Enum, to_state: Enum, proposal_id: str | None = None) -> None:
        """Build the error for a forbidden Proposal transition.

        Args:
            from_state: The ProposalState the machine was in.
            to_state: The ProposalState a caller asked to move to.
            proposal_id: The proposal's id, when the caller has it, folded into the message so
                the failure is debuggable without a stack trace.
        """
        # A missing proposal_id still produces a full sentence; codingrules section 10 wants the
        # identifiers needed to debug, not a placeholder, so the clause is only added when known.
        subject = f" for proposal {proposal_id}" if proposal_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the proposal state machine."
        )
        super().__init__(message)
        self.from_state = from_state
        self.to_state = to_state
        self.proposal_id = proposal_id


class DiffApplyError(CappingError):
    """Raise when `hivemind.supervision.capping.diff.apply_unified_diff` cannot apply a hunk.

    Covers a context or removal line that does not match the file it is being applied to, and a
    diff with no recognisable hunks at all.
    """

    code: ClassVar[str] = "hivemind.supervision.capping.diff_apply_error"

    def __init__(self, path: str, reason: str) -> None:
        """Build the error for a diff that does not apply cleanly.

        Args:
            path: The target path the diff was being applied to, for a caller that applies more
                than one path's diff in a loop.
            reason: What went wrong, folded into the message so the failure is debuggable without
                a stack trace.
        """
        super().__init__(f"Could not apply diff to {path!r}: {reason}.")
        self.path = path
        self.reason = reason
