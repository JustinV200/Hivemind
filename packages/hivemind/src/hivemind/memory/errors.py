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
`BeeBreadEntryNotFoundError` (roadmap step 4.2) covers a Bee Bread (the warm memory tier) lookup by
id that names no stored entry, raised by `hivemind.memory.store.protocol.MemoryStore.
get_bee_bread_entry`. `SummaryOfSummaryError`, `EmptyCompactionError` and `TooManySourcesError`
(roadmap step 4.3) are `hivemind.memory.compact.compact`'s own three refusals: a source that is
itself already a `BeeBreadEntryKind.SUMMARY` (compaction is one level only, docs/adr/0022), no
sources at all, and more sources than one entry may reference (`hivemind.memory.bee_bread.entry.
MAX_REF_IDS`). `InvalidWaxTransitionError` (roadmap step 4.2a) is `hivemind.memory.cell_wax.state.
assert_transition`'s own refusal, mirroring `hivemind.forage.errors.InvalidGrantTransitionError`
exactly; `WaxTextTooLongError` is `hivemind.memory.cell_wax.writes.propose_wax`'s refusal of a
proposal whose text exceeds the manifest's own (possibly lower than the wire shape's)
`[memory] wax_text_cap_chars`. Roadmap step 10.6d (ADR-0035) adds the taint label's four:
`TaintedMemoryError` is what every Handoff loader and item lookup raises for an item labelled
tainted (refused outright, whatever its relevance or clearance), `InvalidTaintTransitionError` is
`hivemind.memory.taint.state.assert_transition`'s refusal of an illegal label edge,
`TaintTargetNotFoundError` names a taint target no ledger holds, and `TaintJudgeError` is the
taint judge failing to produce a verdict at all, which leaves the item tainted (fail closed).

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

from typing import TYPE_CHECKING, ClassVar

from hivemind.cell import HoneyClearance
from hivemind.common.errors import (
    ConflictError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)

if TYPE_CHECKING:
    # Type-checking only: hivemind.memory.cell_wax.state imports this module (for
    # InvalidWaxTransitionError), so a real, eager import of WaxState here would be circular.
    # from __future__ import annotations already makes every annotation below a string at
    # runtime, so InvalidWaxTransitionError.__init__ never needs to resolve the name.
    from hivemind.memory.cell_wax.state import WaxState
    from hivemind.memory.taint.marker import TaintState

__all__ = [
    "BeeBreadEntryNotFoundError",
    "ClearanceError",
    "EmptyCompactionError",
    "HandoffNotFoundError",
    "InvalidTaintTransitionError",
    "InvalidWaxTransitionError",
    "MemoryTierError",
    "NoteTooLongError",
    "SummaryOfSummaryError",
    "TaintJudgeError",
    "TaintTargetNotFoundError",
    "TaintedMemoryError",
    "TooManySourcesError",
    "WaxNotFoundError",
    "WaxTextTooLongError",
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


class BeeBreadEntryNotFoundError(NotFoundError):
    """Raise when a lookup by id finds no matching BeeBreadEntry in the warm memory tier."""

    code: ClassVar[str] = "hivemind.memory.bee_bread_entry_not_found"

    def __init__(self, entry_id: str) -> None:
        """Build the error for a missing Bee Bread entry.

        Args:
            entry_id: The BeeBreadEntry id that was looked up and not found.
        """
        super().__init__(f"No BeeBreadEntry with id {entry_id!r} exists in the memory tables.")
        self.entry_id = entry_id


class SummaryOfSummaryError(MemoryTierError):
    """Raise when `hivemind.memory.compact.compact` is offered a source that is already a summary.

    Compaction is one level only (docs/adr/0022-memory-tiers-relevance-and-compaction.md: "never
    from a previous summary"), so a `BeeBreadEntry` whose `kind` is `BeeBreadEntryKind.SUMMARY`
    is refused as a source rather than folded into a second-level summary.
    """

    code: ClassVar[str] = "hivemind.memory.summary_of_summary"

    def __init__(self, entry_id: str) -> None:
        """Build the error for a summary offered as a compaction source.

        Args:
            entry_id: The offending `BeeBreadEntry.id`.
        """
        super().__init__(
            f"BeeBreadEntry {entry_id!r} is already a SUMMARY; compaction never summarises a "
            "summary (one level only)."
        )
        self.entry_id = entry_id


class EmptyCompactionError(MemoryTierError):
    """Raise when `hivemind.memory.compact.compact` is given no source entries to summarise."""

    code: ClassVar[str] = "hivemind.memory.empty_compaction"

    def __init__(self) -> None:
        """Build the error for a compaction request with no sources."""
        super().__init__("compact() was given no source entries; there is nothing to summarise.")


class TooManySourcesError(MemoryTierError):
    """Raise when `hivemind.memory.compact.compact` is given more sources than one entry allows.

    `hivemind.memory.bee_bread.entry.MAX_REF_IDS` bounds how many source ids one `BeeBreadEntry`
    may reference; a caller with a larger backlog (a House Bee sweep, roadmap step 4.3) batches it
    into several `compact()` calls instead.
    """

    code: ClassVar[str] = "hivemind.memory.too_many_sources"

    def __init__(self, count: int, limit: int) -> None:
        """Build the error for an oversized compaction request.

        Args:
            count: How many sources were offered.
            limit: The cap they exceeded (`MAX_REF_IDS`).
        """
        super().__init__(
            f"compact() was given {count} source entries, over the {limit}-entry limit for one "
            "summary; split into smaller batches."
        )
        self.count = count
        self.limit = limit


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


class WaxNotFoundError(NotFoundError):
    """Raise when a lookup by id finds no matching Cell Wax note in the memory tables."""

    code: ClassVar[str] = "hivemind.memory.wax_not_found"

    def __init__(self, wax_id: str) -> None:
        """Build the error for a missing Cell Wax note.

        Args:
            wax_id: The note id that was looked up and not found.
        """
        super().__init__(f"No Cell Wax note with id {wax_id!r} exists in the memory tables.")
        self.wax_id = wax_id


class InvalidWaxTransitionError(ConflictError):
    """Raise when `hivemind.memory.cell_wax.state.assert_transition` is asked for an illegal edge.

    Mirrors `hivemind.forage.errors.InvalidGrantTransitionError` exactly: a Cell Wax note's state
    machine (Appendix C, "Cell Wax note" row) has exactly one legal set of edges, and every other
    move -- a REJECTED note moved anywhere, PROPOSED straight to EXPIRED -- raises this instead of
    silently applying.
    """

    code: ClassVar[str] = "hivemind.memory.invalid_wax_transition"

    def __init__(
        self, from_state: WaxState, to_state: WaxState, *, subject_id: str | None = None
    ) -> None:
        """Build the error for an illegal Cell Wax transition.

        Args:
            from_state: The note's state before the attempted move.
            to_state: The state the caller asked to move it to.
            subject_id: The note's own id, when the caller has it.
        """
        subject = f" ({subject_id})" if subject_id is not None else ""
        super().__init__(
            f"Cell Wax note{subject} cannot move from {from_state.value} to {to_state.value}: "
            "no such edge in WaxState.TRANSITIONS."
        )
        self.from_state = from_state
        self.to_state = to_state


class WaxTextTooLongError(MemoryTierError):
    """Raise when a Cell Wax proposal's text is longer than the manifest's own wax_text_cap_chars.

    Raised by `hivemind.memory.cell_wax.writes.propose_wax` before a `CellWax` is even
    constructed: the manifest's `[memory] wax_text_cap_chars` may be lower than the wire shape's
    own hard ceiling (`waggle.messages.cell.wax.MAX_WAX_TEXT_CHARS`), and a runtime manifest value
    cannot be expressed as a pydantic `Field(max_length=...)` fixed at import time.
    """

    code: ClassVar[str] = "hivemind.memory.wax_text_too_long"

    def __init__(self, length: int, limit: int) -> None:
        """Build the error for an oversized Cell Wax proposal.

        Args:
            length: How long the offending text was, in characters.
            limit: The manifest's own cap it exceeded.
        """
        super().__init__(f"Cell Wax text is {length} chars, over the {limit}-char manifest cap.")
        self.length = length
        self.limit = limit


class TaintedMemoryError(PermissionDeniedError):
    """Raise when a Handoff loader or an item lookup meets an item labelled tainted.

    Roadmap step 10.6d: a tainted checkpoint, Handoff, episode record or Bee Bread entry (and, from
    phase 7, a Nectar or Honey item) is refused outright until a judge verdict on the taint rubric
    clears it, so an injected instruction can never ride a resumed Handoff back into a prompt.
    """

    code: ClassVar[str] = "hivemind.memory.tainted"

    def __init__(self, item_id: str, event_id: str) -> None:
        """Build the error for one refused tainted item.

        Args:
            item_id: The refused item's own id (a Handoff's checkpoint event id, an episode id).
            event_id: The `memory.tainted` trail event that labelled it.
        """
        super().__init__(
            f"Memory item {item_id} is tainted (by trail event {event_id}) and is refused until "
            "a judge verdict on the taint rubric clears it."
        )
        self.item_id = item_id
        self.event_id = event_id


class InvalidTaintTransitionError(ConflictError):
    """Raise when `hivemind.memory.taint.state.assert_transition` is asked for an illegal edge.

    The taint label (Appendix C, "Taint label" row) moves unlabelled or CLEARED -> TAINTED (one
    setter) and TAINTED -> CLEARED (one judge verdict); every other move, tainting an item that is
    already tainted or clearing one that is not, raises this instead of silently applying.
    """

    code: ClassVar[str] = "hivemind.memory.invalid_taint_transition"

    def __init__(
        self, from_state: TaintState | None, to_state: TaintState, *, item_id: str | None = None
    ) -> None:
        """Build the error for an illegal taint-label edge.

        Args:
            from_state: The label's state before the move; None for an unlabelled item.
            to_state: The state the caller asked for.
            item_id: The item's own id, when the caller has it.
        """
        subject = f" {item_id}" if item_id is not None else ""
        before = from_state.value if from_state is not None else "unlabelled"
        super().__init__(
            f"The taint label of item{subject} cannot move from {before} to {to_state.value}: "
            "no such edge in the taint state machine."
        )
        self.from_state = from_state
        self.to_state = to_state


class TaintTargetNotFoundError(NotFoundError):
    """Raise when a taint ledger holds no item for a `TaintTarget`."""

    code: ClassVar[str] = "hivemind.memory.taint_target_not_found"

    def __init__(self, kind: str, item_id: str) -> None:
        """Build the error for a missing taint target.

        Args:
            kind: The target's kind (`TaintedKind` value).
            item_id: The id that was looked up and not found.
        """
        super().__init__(f"No {kind} item with id {item_id!r} exists to label or clear.")
        self.kind = kind
        self.item_id = item_id


class TaintJudgeError(MemoryTierError):
    """Raise when the taint judge cannot produce a verdict at all (its ladder was exhausted).

    Raised by `hivemind.memory.taint.judge.ModelTaintJudge` in place of the model layer's own
    malformed-output error; `hivemind.memory.taint.clear.clear_taint` catches it and leaves the
    item tainted, since only a verdict may clear one (fail closed).
    """

    code: ClassVar[str] = "hivemind.memory.taint_judge_failed"

    def __init__(self, detail: str) -> None:
        """Build the error for a judge that could not answer.

        Args:
            detail: Why, bounded by the caller; never the item's own content.
        """
        super().__init__(f"The taint judge could not produce a verdict: {detail}")
        self.detail = detail
