"""Define WaxSeverity (mirrored from waggle) and CellWax: the note itself, at rest.

Cell Wax is a Queen-written caution about one Cell (a unit of compute: a Virtual Cell the Hive
provisions, or a Real Cell, an existing device borrowed for a task and left exactly as found) --
"it marks the cell, it is not the honey inside" (roadmap step 4.2a). ``WaxSeverity`` mirrors
``waggle.messages.cell.wax.WaxSeverity`` member for member (name and value), the same pattern
``hivemind.forage.slots.Effort`` follows for the wire ``Effort`` it travels as: a domain enum a
pure autopilot rule or the relevance scorer can hold without importing a wire message package,
kept from drifting apart from the wire shape by a test that checks every member. ``CellWax`` is
the note itself, as it sits in the ``memory_cell_wax`` table across its whole life (proposed,
written, cleared or expired, or rejected): unlike a Pin or a Note, it carries its own
``hivemind.memory.cell_wax.state.WaxState`` and decision bookkeeping, because it is a state
machine, not a fire-and-forget row. ``origin`` (who noticed it: a bee, a Patrol, or the human),
``decided_by`` (autopilot or an awake episode) and ``clear_cause`` (cleared, expired, or retired
with a destroyed Virtual Cell) are read straight from ``waggle.messages.cell.wax`` rather than
mirrored a second time: nothing here scores or gates on them the way it does on severity, so a
second domain copy would only be more code to keep in sync for no benefit. ``new_wax_id`` mints a
``wax_``-prefixed ULID the way ``waggle.ids.new_id`` mints every other kind, duplicated in
miniature because ``waggle.ids.IdKind`` carries no ``WAX`` member yet (that module's own docstring:
"a note is identified by a wax_-prefixed ULID string until an IdKind for it exists") and
``packages/waggle`` is outside this package's own files to add one to.
``cap_wax_for_hot_state`` is the per-Cell cap roadmap step 4.2a and docs/adr/0022 both name
("bounded by the per-Cell cap... highest severity first, then newest"): pure, so
``hivemind.queen.awake.episode.QueenSources.wax`` (or a Warden's own equivalent) can call it on
whatever ``list_wax`` returns for one Cell before ever handing candidates to hot-state assembly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the ``cell_wax`` sub-package.
    ``CellWax`` is read and written by ``hivemind.memory.cell_wax.writes`` and persisted by
    ``hivemind.memory.store`` (the ``memory_cell_wax`` table); ``cap_wax_for_hot_state`` is called
    by whichever ``hivemind.memory.hot_state.summaries.HotStateSources.wax`` implementation a
    Queen or a Warden builds over its own store. Calls into ``hivemind.cell`` (HoneyClearance),
    ``hivemind.memory.cell_wax.state`` (WaxState), ``waggle.ids``, ``waggle.messages.base`` and
    ``waggle.messages.cell.wax`` (WaxClearCause, WaxDecision, WaxOrigin, WaxSeverity as
    ``WireWaxSeverity``, MAX_WAX_TEXT_CHARS, WAX_ID_PATTERN) and ``waggle.ulid`` only.

Key invariants:
    - WaxSeverity's member names and values are identical to
      ``waggle.messages.cell.wax.WaxSeverity``'s (tests/unit/memory/cell_wax/test_model.py checks
      it member for member), so ``WaxSeverity(wire_value.value)`` always round-trips.
    - ``CellWax.text`` is bounded by ``MAX_WAX_TEXT_CHARS`` (the wire shape's own hard ceiling);
      the manifest's own, possibly lower, ``[memory] wax_text_cap_chars`` is enforced by
      ``hivemind.memory.cell_wax.writes.propose_wax`` before construction, not by this field,
      because the manifest cap is a runtime value a pydantic ``Field`` cannot read.
    - ``cap_wax_for_hot_state`` never reorders its input beyond severity-desc-then-newest, and
      never returns more than ``cap`` items; passing an already-short sequence returns it in that
      same order, unmodified in length.

See Also:
    - .claude/codingrules.md section 6.1, "Cell Wax" row.
    - .claude/roadmap.md step 4.2a for CellWax's field list verbatim.
    - docs/adr/0022-memory-tiers-relevance-and-compaction.md for the per-Cell cap rule this
      module's ``cap_wax_for_hot_state`` implements.
    - hivemind.memory.cell_wax.state for WaxState, the field CellWax.state holds.
    - hivemind.memory.cell_wax.writes for propose_wax, write_wax, reject_wax, clear_wax,
      expire_wax and retire_wax_for_cell, the only functions that construct or transition one.
    - waggle.messages.cell.wax for the wire shapes this module mirrors or reuses.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.memory.cell_wax.state import WaxState
from waggle.clock import Clock
from waggle.messages.base import CellIdField, TaskIdField, UtcDatetime
from waggle.messages.cell.wax import (
    MAX_WAX_TEXT_CHARS,
    WAX_ID_PATTERN,
    WaxClearCause,
    WaxDecision,
    WaxOrigin,
)
from waggle.ulid import RANDOMNESS_BYTES, encode_ulid

MAX_WAX_REASON_CHARS = 500  # A sentence or two, matching every other reason field in the Hive.
_WAX_ID_PREFIX = "wax_"  # No waggle.ids.IdKind.WAX exists yet; see the module docstring.

__all__ = [
    "MAX_WAX_REASON_CHARS",
    "MAX_WAX_TEXT_CHARS",
    "CellWax",
    "WaxSeverity",
    "cap_wax_for_hot_state",
    "new_wax_id",
]


class WaxSeverity(Enum):
    """How much a Cell Wax note weighs on placement; mirrors waggle's, member for member."""

    NOTE = "NOTE"  # Recorded, no effect on placement.
    CAUTION = "CAUTION"  # A penalty in placement.
    BLOCK = "BLOCK"  # Exclusion from placement; every BLOCK is an awake decision.


# Highest first: BLOCK outranks CAUTION outranks NOTE, both for placement penalties and for the
# hot-state severity bonus (hivemind.memory.relevance) and the per-Cell cap below.
_SEVERITY_RANK: dict[WaxSeverity, int] = {
    WaxSeverity.NOTE: 0,
    WaxSeverity.CAUTION: 1,
    WaxSeverity.BLOCK: 2,
}


class CellWax(BaseModel):
    """One Cell Wax note, at rest: its content plus its whole lifecycle's bookkeeping.

    Every field this note is ever written or judged with lives here, so a caller reading one row
    back from the store has everything ``hivemind.memory.cell_wax.writes`` needs to walk it to its
    next state, with no second lookup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=WAX_ID_PATTERN, description="This note's own id: a wax_-prefixed ULID.")
    cell_id: CellIdField = Field(description="The Cell this note is about.")
    state: WaxState = Field(description="Where this note is in its own life.")
    severity: WaxSeverity = Field(description="How much it weighs on placement, once WRITTEN.")
    text: str = Field(
        min_length=1,
        max_length=MAX_WAX_TEXT_CHARS,
        description="The caution itself; the manifest's own [memory] wax_text_cap_chars may cap "
        "it lower, enforced by propose_wax before construction.",
    )
    proposer: str | None = Field(
        description="The proposing bee's own id (a WorkerId or WardenId, as text); None exactly "
        "when origin is HUMAN."
    )
    origin: WaxOrigin = Field(description="Who noticed the caution: a bee, a Patrol, or the human.")
    reason: str = Field(
        max_length=MAX_WAX_REASON_CHARS,
        description="Why the proposer believes it; set once, at "
        "proposal, and never overwritten by a later decision (that reason travels on the "
        "accompanying trail event instead).",
    )
    clearance: HoneyClearance = Field(description="This note's data-sensitivity label.")
    task_id: TaskIdField | None = Field(
        default=None, description="The task during which it was noticed, for provenance."
    )
    expires_at: UtcDatetime | None = Field(
        default=None, description="When this note expires on its own; None for standing wax."
    )
    proposed_at: UtcDatetime = Field(description="When it was proposed.")
    decided_by: WaxDecision | None = Field(
        default=None, description="By rule or by a model episode; set once WRITTEN, else None."
    )
    decided_at: UtcDatetime | None = Field(
        default=None, description="When it was written or rejected; None while still PROPOSED."
    )
    clear_cause: WaxClearCause | None = Field(
        default=None, description="Why it left WRITTEN; set only once CLEARED or EXPIRED."
    )
    cleared_at: UtcDatetime | None = Field(
        default=None, description="When it was cleared or expired; None until then."
    )


def new_wax_id(clock: Clock) -> str:
    """Mint a fresh ``wax_``-prefixed ULID, timestamped by ``clock``.

    Mirrors ``waggle.ids.new_id``'s own logic in miniature: no ``IdKind.WAX`` member exists yet
    (module docstring), and ``packages/waggle`` is outside this dispatch's own files to add one to.

    Args:
        clock: Injected clock so the id's timestamp is deterministic in tests.

    Returns:
        A ``"wax_<26-char ULID>"`` string matching ``waggle.messages.cell.wax.WAX_ID_PATTERN``.
    """
    timestamp_ms = int(clock.now().timestamp() * 1000)
    randomness = secrets.token_bytes(RANDOMNESS_BYTES)
    return f"{_WAX_ID_PREFIX}{encode_ulid(timestamp_ms, randomness)}"


def cap_wax_for_hot_state(items: Sequence[CellWax], cap: int) -> tuple[CellWax, ...]:
    """Return at most ``cap`` of ``items``, highest severity first, then newest (docs/adr/0022).

    Args:
        items: Candidate wax for one Cell, in any order; typically every WRITTEN, unexpired note
            for that Cell.
        cap: The manifest's own per-Cell cap (``[memory] cell_wax_cap``); must be >= 0.

    Returns:
        At most ``cap`` items from ``items``, ordered severity-desc then newest-first. The rest
        stay in the table (docs/adr/0022: "they stay in the table and ripen into Honey later"),
        this function only decides what enters hot state.
    """
    ranked = sorted(
        items,
        key=lambda wax: (-_SEVERITY_RANK[wax.severity], -wax.proposed_at.timestamp()),
    )
    return tuple(ranked[:cap])
