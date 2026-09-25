"""Define the flat summary models hot-state packing scores, plus HotStateSources and the wrappers.

`hivemind.memory` sits below `hivemind.brood_chamber` and `hivemind.supervision` in the layer
table but on the very same layer as both (codingrules section 4); with no ADR listing that edge,
memory may not import either directly. So instead of packing real `Task`, `Alarm` and `Question`
objects, `hivemind.memory.hot_state.packing.assemble` packs these flat summary models, which the
layer above (queen, wardens) fills in from its own `Task`/`Alarm`/`Question` records before calling
`assemble`. `CellWaxSummary` (roadmap step 4.2a) is the one flat summary this module owns the
source model of (`hivemind.memory.cell_wax.CellWax`) rather than mirroring an out-of-layer record;
`HotStateSources.wax` returns it only for a Cell in `AssembleRequest.cells_in_play`, so a caution
about a Cell not currently a placement or assignment candidate never becomes a hot-state
candidate at all (docs/adr/0022). `Principal` (who is reading), `TokenBudget` (how much room there
is) and `TriggerEvent` (what triggered this episode) are the other inputs `assemble` needs;
`HotStateSources` is the Protocol a caller implements over its own stores to hand all of the above
to `assemble` without memory ever reading brood_chamber or supervision itself.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by hivemind.memory.hot_state.
    packing.assemble; implemented (HotStateSources) and constructed (the summary models) by the
    Queen and a Warden, each over their own stores (a later roadmap step). Calls into hivemind.cell
    (HoneyClearance), hivemind.forage (ModelSlot), hivemind.memory.notes (Note), hivemind.memory.
    pins (Pin) and waggle only.

Key invariants:
    - Every summary model carries a `clearance` field, like every other memory-tier row
      (codingrules section 8.9): `assemble` filters every candidate by it before anything else.
    - A `DecisionSummary` carries the taint label of the record it came from, and a
      `TriggerEvent`'s outside text travels with its scan verdict, so `assemble` can refuse the
      one and apply the other without reading any store itself (roadmap steps 10.6b, 10.6d).
    - AlarmSummary.raised_at and QuestionSummary.asked_at exist so every hot-state category can be
      ordered by recency the same way (codingrules section 8.9's "packed by relevance... until the
      budget fills"); they are this module's own addition to the roadmap's compact field list,
      without which "newest first" would have no timestamp to compare for two of the five
      categories (flagged in this dispatch's report).

See Also:
    - .claude/codingrules.md section 4 for the same-layer-needs-an-ADR rule this module works
      around by defining its own summary shapes instead of importing brood_chamber/supervision.
    - .claude/codingrules.md section 8.9 for the memory-tier clearance and recency rules this
      module's fields exist to carry.
    - hivemind.memory.hot_state.packing for assemble, the one reader of every model here.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.memory.handoff import Handoff
from hivemind.memory.hot_state.untrusted import UntrustedText
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.taint.marker import TaintMarker
from waggle.ids import CellId
from waggle.messages.base import AlarmIdField, CellIdField, MessageIdField, TaskIdField, UtcDatetime

SUMMARY_TITLE_CAP_CHARS = 200  # A one-line summary, matching TaskSpec.title's own scale.
SUMMARY_TEXT_CAP_CHARS = 500  # A hot-state summary line is a sentence or two, not the source
# record's own (much larger) cap; ITEM_CAP_CHARS below still governs the final rendered line
# regardless of this per-field cap.
MAX_SUMMARY_OPTIONS = 16  # Matches brood_chamber.questions.MAX_OPTIONS's own scale.
SUMMARY_OPTION_CAP_CHARS = 200  # Matches brood_chamber.questions.MAX_OPTION_CHARS's own scale.
MAX_PRINCIPAL_ID_CHARS = 128  # A bee id or role name.
MAX_PRINCIPAL_ROLE_CHARS = 64
# A hot-state item longer than this becomes a one-line reference instead of being inlined, so one
# bee's huge tool result or long objective never crowds out everything else (codingrules 8.9).
# Matches the manifest's `[memory] item_cap_chars` default (manifest.schema.supervision.
# DEFAULT_ITEM_CAP_CHARS); defined here (not in packing.py) so TokenBudget.item_cap_chars below can
# default to it without packing.py importing back from this module's own consumer.
ITEM_CAP_CHARS = 4_000
# The most of a packing target the cold tier (retrieved Honey hits) may ever take, after hot state
# has packed first: a quarter keeps reference material from outweighing the episode's own state.
DEFAULT_RETRIEVED_FRACTION = 0.25

__all__ = [
    "DEFAULT_RETRIEVED_FRACTION",
    "ITEM_CAP_CHARS",
    "MAX_SUMMARY_OPTIONS",
    "SUMMARY_OPTION_CAP_CHARS",
    "SUMMARY_TEXT_CAP_CHARS",
    "SUMMARY_TITLE_CAP_CHARS",
    "AlarmSummary",
    "CellWaxSummary",
    "DecisionSummary",
    "HotStateSources",
    "Principal",
    "QuestionSummary",
    "TaskSummary",
    "TokenBudget",
    "TriggerEvent",
]

# One multiple-choice option string in a QuestionSummary, capped like brood_chamber.questions'
# own Question.options element type.
_Option = Annotated[str, Field(max_length=SUMMARY_OPTION_CAP_CHARS)]


class Principal(BaseModel):
    """Who a prompt is being assembled for: their slot, clearance ceiling and role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(max_length=MAX_PRINCIPAL_ID_CHARS, description="A bee id or role name.")
    slot: ModelSlot = Field(description="The ModelSlot this principal's episode will run on.")
    clearance: HoneyClearance = Field(description="This principal's allowance ceiling.")
    role: str = Field(max_length=MAX_PRINCIPAL_ROLE_CHARS, description="This principal's role.")


class TokenBudget(BaseModel):
    """How much room an assembled prompt has, before the triggering event is added."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_input_tokens: int = Field(gt=0, description="The slot's window, times a manifest fraction.")
    output_reserve: int = Field(ge=0, description="Tokens reserved for the response.")
    item_cap_chars: int = Field(
        default=ITEM_CAP_CHARS,
        gt=0,
        description="Per-item character cap (manifest [memory] item_cap_chars); oversized items "
        "become one-line references instead of being inlined, and a retrieved hit's excerpt is "
        "cut here.",
    )
    retrieved_fraction: float = Field(
        default=DEFAULT_RETRIEVED_FRACTION,
        gt=0,
        le=1,
        description="The most of the packing target (max_input_tokens - output_reserve) the "
        "RETRIEVED section may take; retrieved hits pack after hot state, into whatever is left "
        "up to this share, and never take room from hot state.",
    )


class TriggerEvent(BaseModel):
    """What triggered this episode: a short summary and an optional reference to more detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(description="What kind of thing triggered this episode.")
    summary: str = Field(description="A short description of the trigger.")
    payload_ref: str | None = Field(
        default=None,
        description="A reference to further detail, when there is more than the summary.",
    )
    clearance: HoneyClearance = Field(description="This trigger's data-sensitivity label.")
    untrusted: UntrustedText | None = Field(
        default=None,
        description="Outside text the trigger carries (a human's chat words, roadmap step 10.5), "
        "with its scan verdict; assemble renders it after the summary, fenced, labelled harder "
        "or withheld by that verdict (roadmap step 10.6b). None when the trigger carries none.",
    )


class TaskSummary(BaseModel):
    """A flat summary of one active task, for hot-state packing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TaskIdField = Field(description="The task's id.")
    title: str = Field(max_length=SUMMARY_TITLE_CAP_CHARS, description="The task's title.")
    status: str = Field(
        description="The task's status, as a plain string (memory does not mirror TaskStatus)."
    )
    objective: str = Field(
        max_length=SUMMARY_TEXT_CAP_CHARS, description="A capped summary of the objective."
    )
    updated_at: UtcDatetime = Field(description="When this task was last updated; its recency key.")
    clearance: HoneyClearance = Field(description="The task's data-sensitivity label.")


class AlarmSummary(BaseModel):
    """A flat summary of one open Alarm, for hot-state packing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AlarmIdField = Field(description="The Alarm's id.")
    kind: str = Field(
        description="The Alarm's kind, as a plain string (memory does not mirror AlarmKind)."
    )
    severity: str = Field(description="The Alarm's severity, as a plain string.")
    detail: str = Field(
        max_length=SUMMARY_TEXT_CAP_CHARS, description="A capped summary of the detail."
    )
    attempts: int = Field(ge=0, description="Resolution attempts made so far.")
    task_id: TaskIdField | None = Field(
        default=None, description="The task this Alarm concerns, if any."
    )
    clearance: HoneyClearance = Field(description="The Alarm's data-sensitivity label.")
    raised_at: UtcDatetime = Field(description="When it was raised; its recency key.")


class QuestionSummary(BaseModel):
    """A flat summary of one pending question, for hot-state packing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: MessageIdField = Field(description="The question's id.")
    task_id: TaskIdField = Field(description="The task this question blocks.")
    text: str = Field(
        max_length=SUMMARY_TEXT_CAP_CHARS, description="A capped summary of the question."
    )
    options: tuple[_Option, ...] = Field(
        default=(),
        max_length=MAX_SUMMARY_OPTIONS,
        description="The question's offered choices, if closed.",
    )
    clearance: HoneyClearance = Field(description="The question's data-sensitivity label.")
    asked_at: UtcDatetime = Field(description="When it was asked; its recency key.")


class DecisionSummary(BaseModel):
    """A flat summary of one recent decision (an episode's outcome), for hot-state packing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    episode_id: str = Field(description="The EpisodeRecord.id this decision summarises.")
    at: UtcDatetime = Field(description="When the episode ran; its recency key.")
    decision: str = Field(
        max_length=SUMMARY_TEXT_CAP_CHARS, description="A capped summary of the decision."
    )
    action: str = Field(
        max_length=SUMMARY_TEXT_CAP_CHARS, description="A capped summary of the action taken."
    )
    clearance: HoneyClearance = Field(description="The decision's data-sensitivity label.")
    tainted: TaintMarker | None = Field(
        default=None,
        description="The taint label of the record this decision came from (an episode record, "
        "a resumed Handoff); a TAINTED decision is refused by assemble outright (10.6d).",
    )


class CellWaxSummary(BaseModel):
    """A flat summary of one WRITTEN Cell Wax note, for hot-state packing (roadmap step 4.2a).

    Only ever a candidate for a Cell in `hivemind.memory.hot_state.packing.AssembleRequest.
    cells_in_play`: `HotStateSources.wax` below is called with exactly that set, and a caller
    (`hivemind.queen.awake.episode.QueenSources.wax`) that queries no Cell returns none of these,
    so a caution about a Cell not in play never becomes a candidate at all (docs/adr/0022).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="The note's own id (a wax_-prefixed ULID).")
    cell_id: CellIdField = Field(description="The Cell this note is about.")
    severity: str = Field(
        description="NOTE, CAUTION or BLOCK, as a plain string (memory does not mirror "
        "hivemind.memory.cell_wax.WaxSeverity here, matching AlarmSummary.severity's own shape)."
    )
    text: str = Field(
        max_length=SUMMARY_TEXT_CAP_CHARS, description="A capped summary of the caution."
    )
    clearance: HoneyClearance = Field(description="The note's data-sensitivity label.")
    written_at: UtcDatetime = Field(description="When the Queen wrote it; its recency key.")


class HotStateSources(Protocol):
    """What `assemble` reads: the Queen and a Warden each implement this over their own stores."""

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """Return every active task, as flat summaries."""
        ...

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """Return every open Alarm, as flat summaries."""
        ...

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """Return every pending question, as flat summaries."""
        ...

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        """Return WRITTEN Cell Wax for `cells` only, capped per Cell, as flat summaries.

        Args:
            cells: The Cells currently a candidate for placement or assignment
                (`AssembleRequest.cells_in_play`); empty means none, and this must then return
                nothing (docs/adr/0022: "wax scores into hot state only while its Cell is a
                candidate").

        Returns:
            Every WRITTEN, unexpired note for a Cell in `cells`, already bounded by the manifest's
            own per-Cell cap (`hivemind.memory.cell_wax.cap_wax_for_hot_state`); a Cell not named
            in `cells` never appears here at all.
        """
        ...

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """Return up to `limit` recent decisions, as flat summaries."""
        ...

    async def pins(self) -> tuple[Pin, ...]:
        """Return every pin visible to this source, from the memory store."""
        ...

    async def notes(self) -> tuple[Note, ...]:
        """Return every note visible to this source, from the memory store."""
        ...

    async def handoff(self) -> Handoff | None:
        """Return the Handoff this episode is resuming from, or None when it is not a resume.

        Defect 2 (this dispatch's own report): before this method existed, a resuming Drone's
        prompt only ever saw a resumed Handoff's `decisions` (`hivemind.workers.roles.drone.
        sources.DroneSources.recent_decisions`); `goal`, `progress`, `next_steps`, `do_not_redo`
        and every other field never reached the model at all. `hivemind.memory.hot_state.packing.
        assemble` renders whatever this returns into its own delimited section of `HOT_STATE`
        (`packing._render_handoff`), unconditionally -- never scored, never dropped for budget,
        only bounded by `AssembleRequest.budget.item_cap_chars` -- so a resuming bee always sees
        what a prior attempt already did and must not redo.

        Returns:
            The resumed Handoff, verbatim, or None for an episode that is not resuming one at all
            (most callers: `hivemind.queen.awake.episode.QueenSources` and `hivemind.wardens.
            ticks.heartbeat._WardenHotState` always return None here, since neither ever resumes
            from a Worker's own Handoff).
        """
        ...
