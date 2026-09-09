"""Define ForageError and the Forage package's own error tree.

Forage (`hivemind.forage`) models the Hive's capacity as data and computes grants over it. This
module holds the ways a caller can misuse that data on purpose: naming a Forage map (the catalogue
of every source that can serve a model) source id that map does not hold, or asking the grant state
machine (`grant_state.TRANSITIONS`) for an edge it does not have. `AllocationError` is the pure
allocator's own catch-all for an input combination `forage.allocate.grant` cannot make sense of
(codingrules section 10: every subsystem's errors descend from one root so a caller several layers
up can catch `ForageError` alone and know it caught anything this package raised on purpose).

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Raised by `hivemind.forage.map`
    (`UnknownSourceError`), `hivemind.forage.allocate` (`AllocationError`) and
    `hivemind.forage.grant_state` (`InvalidGrantTransitionError`). Imported by every caller of
    those modules that wants to catch a Forage-specific failure.

Key invariants:
    - Every ForageError subclass sets its own `code`; none shares a code with another.
    - UnknownSourceError subclasses `hivemind.common.errors.NotFoundError` and
      InvalidGrantTransitionError subclasses `ConflictError`, because a missing source id and an
      illegal state transition each map cleanly onto one of those base categories; AllocationError
      does not, because "the allocator's inputs do not add up" is specific to `forage.allocate` and
      no base category fits it better than the plain root.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError, NotFoundError and ConflictError, the roots this
      module's classes descend from.
    - hivemind.forage.grant_state for GrantState and TRANSITIONS, the table
      InvalidGrantTransitionError reports.
    - hivemind.forage.map for ForageMap, UnknownSourceError's raiser.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError

__all__ = [
    "AllocationError",
    "ForageError",
    "InvalidGrantTransitionError",
    "UnknownSourceError",
]


class ForageError(HiveMindError):
    """Root of every error `hivemind.forage` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.forage.error"


class UnknownSourceError(NotFoundError):
    """Raise when a Forage map lookup by source id finds no matching `ModelSource`."""

    code: ClassVar[str] = "hivemind.forage.unknown_source"

    def __init__(self, source_id: str) -> None:
        """Build the error for a missing Forage map source.

        Args:
            source_id: The map entry key that was looked up and not found.
        """
        super().__init__(f"No Forage map source with id {source_id!r} is known.")
        self.source_id = source_id


class AllocationError(ForageError):
    """Raise when `hivemind.forage.allocate.grant` cannot make sense of its own inputs.

    Covers a footprint whose cpu or memory cost is zero or negative against a divide that needs a
    positive denominator, and any other input combination the pure allocator finds internally
    inconsistent rather than merely "grants nothing" -- a request that fits within capacity but
    reduces to zero sub-bees is not an error, it is a valid (if unhelpful) grant.
    """

    code: ClassVar[str] = "hivemind.forage.allocation_error"


class InvalidGrantTransitionError(ConflictError):
    """Raise when `grant_state`'s table is asked for an edge it does not have.

    Mirrors `hivemind.brood_chamber.errors.InvalidTransitionError`'s shape; Forage keeps its own
    copy rather than sharing that one because `forage` sits below `brood_chamber` in the layer
    table (codingrules section 4) and may not import it.
    """

    code: ClassVar[str] = "hivemind.forage.invalid_grant_transition"

    def __init__(self, from_state: Enum, to_state: Enum, subject_id: str | None = None) -> None:
        """Build the error for a forbidden Forage grant transition.

        Args:
            from_state: The GrantState the machine was in.
            to_state: The GrantState a caller asked to move to.
            subject_id: The grant's `GrantId`, when the caller has it, folded into the message so
                the failure is debuggable without a stack trace.
        """
        # A missing subject_id still produces a full sentence; codingrules section 10 wants the
        # ids needed to debug, not a placeholder, so the clause is only added when one is known.
        subject = f" for {subject_id}" if subject_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the Forage grant state machine."
        )
        super().__init__(message)
        self.from_state = from_state
        self.to_state = to_state
        self.subject_id = subject_id
