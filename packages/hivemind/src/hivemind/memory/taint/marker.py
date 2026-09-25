"""Define the taint label: TaintMarker, its TaintSource and TaintState, and what it can be put on.

Roadmap step 10.6d (ADR-0043): memory written while a bee may have been compromised (under an
injected instruction) must never come back through a later prompt. `TaintMarker` is the one label
that says so: which of the three closed `TaintSource`s set it (the Queen isolating a Cell, a
quarantine of one bee, or the Queen acting on a Guard report about a Honey item), why, the
`memory.tainted` trail event that set it and when; and, once a judge verdict on the taint rubric
cleared it, the `memory.taint_cleared` event and when. The same marker type rides on every
taintable item: a stored Handoff (a checkpoint's document), an episode record and a checkpoint's
Bee Bread deposits today, and a Nectar deposit or a Honey item from phase 7 (`TaintedKind` names
all five). `TaintTarget` addresses one such item for the setter and the clearer. A marker that is
TAINTED refuses its item from every prompt and every Handoff loader; a CLEARED one keeps the
history on the item without refusing it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. Carried
    by `hivemind.memory.handoff.Handoff`, `hivemind.memory.episodes.EpisodeRecord`,
    `hivemind.memory.bee_bread.BeeBreadEntry`, `hivemind.memory.hot_state` summaries and
    retrieved items; built only by `hivemind.memory.taint.set` and `.clear`. Calls into
    `hivemind.common.errors` and waggle only (and pydantic), so every memory model can carry it
    without an import cycle.

Key invariants:
    - A marker is CLEARED exactly when it carries its clearing event and time; a TAINTED one
      carries neither.
    - `refuses` is True only for TAINTED: that is the one check every reader makes.
    - Nothing outside `hivemind.memory.taint` constructs a marker; a test walks the source tree to
      hold every module to that (tests/unit/memory/taint/test_only_setter.py).

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.memory.taint.state for the label's transition table (Appendix C, "Taint label").
    - hivemind.memory.taint.set and .clear for the one setter and the one clearer.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.common.errors import InvariantViolationError
from waggle.ids import IdKind
from waggle.messages.base import EventIdField, UtcDatetime, check_id

MAX_TAINT_REASON_CHARS = 300  # A sentence naming ids, never content; well inside a trail string.
MAX_ITEM_ID_CHARS = 64  # A prefixed ULID, like every other id in the Hive.

__all__ = [
    "MAX_ITEM_ID_CHARS",
    "MAX_TAINT_REASON_CHARS",
    "TaintMarker",
    "TaintSource",
    "TaintState",
    "TaintTarget",
    "TaintedKind",
    "is_refused",
    "require_unlabelled",
]


class TaintSource(Enum):
    """Who may label memory tainted: a closed set of three, and nothing else (ADR-0043)."""

    ISOLATION = "isolation"  # The Queen isolating one Cell (queen/isolation/, roadmap 10.6a).
    QUARANTINE = "quarantine"  # The one Quarantine code path in wardens/ (roadmap 10.6c).
    GUARD_REPORT = "guard_report"  # The Queen on a Guard report about a Honey item (10.6, phase 7).


class TaintState(Enum):
    """The label's two states; an item with no marker is simply unlabelled."""

    TAINTED = "tainted"  # Refused by every prompt and every Handoff loader.
    CLEARED = "cleared"  # A judge verdict on the taint rubric cleared it; history kept.


class TaintedKind(Enum):
    """What kind of memory item a marker sits on."""

    HANDOFF = "handoff"  # A stored Handoff, keyed by its memory.checkpoint event id.
    EPISODE = "episode"  # An EpisodeRecord: one awake episode's or autopilot decision's thinking.
    BEE_BREAD = "bee_bread"  # A checkpoint's Bee Bread deposit: its index, its transcript.
    NECTAR = "nectar"  # Phase 7 seam: a Nectar deposit in the Honey Store (roadmap 7.4).
    HONEY = "honey"  # Phase 7 seam: a ripened Honey item (roadmap 7.7).


class TaintMarker(BaseModel):
    """The one taint label: source, reason, the event that set it and when; and, once, clearing.

    Crosses into every taintable memory model as its `tainted` field and into the memory tables
    as a column beside the row's clearance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: TaintState = Field(description="TAINTED (refused) or CLEARED (usable, history kept).")
    source: TaintSource = Field(description="Which of the three setters labelled it.")
    reason: str = Field(
        min_length=1,
        max_length=MAX_TAINT_REASON_CHARS,
        description="Why, naming ids only (the report, the Cell, the episode), never content.",
    )
    event_id: EventIdField = Field(description="The memory.tainted trail event that set it.")
    at: UtcDatetime = Field(description="When it was set.")
    cleared_event_id: EventIdField | None = Field(
        default=None, description="The memory.taint_cleared trail event, once a judge cleared it."
    )
    cleared_at: UtcDatetime | None = Field(default=None, description="When it was cleared.")

    @property
    def refuses(self) -> bool:
        """Whether this label refuses its item from prompts and loaders (TAINTED only)."""
        return self.state is TaintState.TAINTED

    @model_validator(mode="after")
    def _cleared_exactly_when_clearing_is_recorded(self) -> TaintMarker:
        """Refuse a CLEARED marker without its clearing event, or a TAINTED one with it."""
        recorded = self.cleared_event_id is not None and self.cleared_at is not None
        partial = (self.cleared_event_id is None) != (self.cleared_at is None)
        if partial or recorded != (self.state is TaintState.CLEARED):
            raise ValueError(
                "a CLEARED marker carries both cleared_event_id and cleared_at; a TAINTED one "
                "carries neither."
            )
        return self


class TaintTarget(BaseModel):
    """One taintable item, addressed by kind and id, for the setter and the clearer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TaintedKind = Field(description="What kind of item it is.")
    item_id: str = Field(
        max_length=MAX_ITEM_ID_CHARS,
        description="The item's own id (a Handoff's checkpoint event id, an episode id, ...); "
        "the subject of its memory.tainted and memory.taint_cleared events.",
    )

    @field_validator("item_id")
    @classmethod
    def _is_a_hive_id(cls, value: str) -> str:
        """Accept any well-formed `<kind>_<ulid>` id: the trail must be able to name it."""
        for kind in IdKind:
            if value.startswith(f"{kind.value}_"):
                return check_id(value, kind)
        raise ValueError(f"item_id {value!r} does not start with a known IdKind prefix.")


def is_refused(marker: TaintMarker | None) -> bool:
    """Return whether an item carrying `marker` must be refused (labelled and TAINTED).

    Args:
        marker: The item's `tainted` field; None for an item never labelled.

    Returns:
        True only for a TAINTED marker.
    """
    return marker is not None and marker.refuses


def require_unlabelled(marker: TaintMarker | None, item_id: str) -> None:
    """Refuse to store a new item that arrives already labelled: only a ledger labels.

    A bee writes a Handoff, an episode record or a Bee Bread entry unlabelled; the label is only
    ever written afterwards, by `write_taint` for the one setter or the one clearer. A new item
    carrying a marker would be a label no `memory.tainted` event records, so every store's
    insert path calls this first.

    Args:
        marker: The new item's `tainted` field.
        item_id: The item's own id, for the error message.

    Raises:
        InvariantViolationError: `marker` is set.
    """
    if marker is not None:
        raise InvariantViolationError(
            f"Memory item {item_id} arrived labelled {marker.state.value}; a new item is stored "
            "unlabelled, and only hivemind.memory.taint writes a label (with its trail event)."
        )
