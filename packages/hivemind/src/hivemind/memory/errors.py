"""Define memory's own error tree: clearance refusals, missing Handoffs, oversized notes.

`hivemind.memory` (memory tiers: the working, hot and warm layers between a bee's model call and
the Honey Store's cold, ripened knowledge) roots its errors here, like every other subsystem
(codingrules section 10). The root is named `MemoryTierError`, not `MemoryError`, because the
latter shadows a Python builtin -- codingrules section 10 wins over the shorter name a first draft
might reach for. `ClearanceError` is the one a Night Veil bee (a Tier-2 Comb Shield Cell, restricted
to C0/C1 data) or any under-cleared reader hits when it asks for a Handoff (a resumable snapshot of
a bee's work) above its own allowance; codingrules section 8.9 requires this refusal, never a
silent downgrade. `HandoffNotFoundError` covers a lookup by the `memory.checkpoint` trail event id
that names no stored Handoff. `NoteTooLongError` is raised directly by `hivemind.memory.notes.Note`
itself (not wrapped in a `pydantic.ValidationError`; see that module for why), so a bee's
note-writing tool catches one typed error regardless of which check fires.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by hivemind.memory.checkpoint
    (ClearanceError, HandoffNotFoundError via the store) and hivemind.memory.notes
    (NoteTooLongError). Calls into hivemind.cell (for HoneyClearance) and hivemind.common.errors
    only.

Key invariants:
    - Every MemoryTierError subclass sets its own `code`; none shares a code with another.
    - ClearanceError always carries both the item's clearance and the reader's allowance, so the
      message never needs a second lookup to explain the refusal.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - .claude/codingrules.md section 8.9 for the clearance-filtering rule ClearanceError enforces.
    - hivemind.common.errors for HiveMindError, NotFoundError and PermissionDeniedError, the roots
      this module's classes descend from.
    - hivemind.memory.checkpoint for read_handoff, ClearanceError's and HandoffNotFoundError's
      caller.
    - hivemind.memory.notes for Note, which raises NoteTooLongError from its own field validator.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.cell import HoneyClearance
from hivemind.common.errors import HiveMindError, NotFoundError, PermissionDeniedError

__all__ = [
    "ClearanceError",
    "HandoffNotFoundError",
    "MemoryTierError",
    "NoteTooLongError",
]


class MemoryTierError(HiveMindError):
    """Root of every error `hivemind.memory` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.memory.error"


class ClearanceError(PermissionDeniedError):
    """Raise when a reader's HoneyClearance allowance is below an item's own label.

    codingrules section 8.9: "the assembler filters by the principal's clearance allowance, so a
    Night Veil bee never sees Royal data in hot state and never resumes from a Royal Handoff."
    `hivemind.memory.checkpoint.read_handoff` raises this for a Handoff the caller asked for by
    reference; `hivemind.memory.hot_state.packing.assemble` filters candidates silently instead of
    raising, because a hot-state item is one of many drawn from a shared pool, never a specific
    document a caller named.
    """

    code: ClassVar[str] = "hivemind.memory.clearance_denied"

    def __init__(self, item_clearance: HoneyClearance, allowance: HoneyClearance) -> None:
        """Build the error for a refused read.

        Args:
            item_clearance: The label on the item the caller tried to read.
            allowance: The reader's own clearance ceiling.
        """
        super().__init__(
            f"Item is labelled {item_clearance.name} but the reader's allowance is only "
            f"{allowance.name}."
        )
        self.item_clearance = item_clearance
        self.allowance = allowance


class HandoffNotFoundError(NotFoundError):
    """Raise when a lookup by event id finds no matching Handoff in the memory tables."""

    code: ClassVar[str] = "hivemind.memory.handoff_not_found"

    def __init__(self, event_id: str) -> None:
        """Build the error for a missing Handoff.

        Args:
            event_id: The `memory.checkpoint` trail event id that was looked up and not found.
        """
        super().__init__(f"No Handoff with event id {event_id!r} exists in the memory tables.")
        self.event_id = event_id


class NoteTooLongError(MemoryTierError):
    """Raise when a Note's text is longer than `hivemind.memory.notes.MAX_NOTE_CHARS`.

    Raised directly from `Note`'s own field validator rather than a bare `ValueError`: pydantic
    only intercepts `ValueError`/`TypeError`/`AssertionError` from a validator and wraps those in
    a `pydantic.ValidationError`, so raising this `HiveMindError` subclass instead makes it
    propagate unchanged -- exactly what a bee's note-writing tool wants to catch by name, without
    inspecting a ValidationError's structure first.
    """

    code: ClassVar[str] = "hivemind.memory.note_too_long"

    def __init__(self, length: int, limit: int) -> None:
        """Build the error for an oversized note.

        Args:
            length: How long the offending text was, in characters.
            limit: The cap it exceeded.
        """
        super().__init__(f"Note text is {length} chars, over the {limit}-char limit.")
        self.length = length
        self.limit = limit
