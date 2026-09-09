"""Define the flat summary models hot-state packing scores, plus HotStateSources and the wrappers.

`hivemind.memory` sits below `hivemind.brood_chamber` and `hivemind.supervision` in the layer
table but on the very same layer as both (codingrules section 4); with no ADR listing that edge,
memory may not import either directly. So instead of packing real `Task`, `Alarm` and `Question`
objects, `hivemind.memory.hot_state.packing.assemble` packs these flat summary models, which the
layer above (queen, wardens) fills in from its own `Task`/`Alarm`/`Question` records before calling
`assemble`. `Principal` (who is reading), `TokenBudget` (how much room there is) and `TriggerEvent`
(what triggered this episode) are the other inputs `assemble` needs; `HotStateSources` is the
Protocol a caller implements over its own stores to hand all of the above to `assemble` without
memory ever reading brood_chamber or supervision itself.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by hivemind.memory.hot_state.
    packing.assemble; implemented (HotStateSources) and constructed (the summary models) by the
    Queen and a Warden, each over their own stores (a later roadmap step). Calls into hivemind.cell
    (HoneyClearance), hivemind.forage (ModelSlot), hivemind.memory.notes (Note), hivemind.memory.
    pins (Pin) and waggle only.

Key invariants:
    - Every summary model carries a `clearance` field, like every other memory-tier row
      (codingrules section 8.9): `assemble` filters every candidate by it before anything else.
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
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from waggle.messages.base import AlarmIdField, MessageIdField, TaskIdField, UtcDatetime

SUMMARY_TITLE_CAP_CHARS = 200  # A one-line summary, matching TaskSpec.title's own scale.
SUMMARY_TEXT_CAP_CHARS = 500  # A hot-state summary line is a sentence or two, not the source
# record's own (much larger) cap; hivemind.memory.hot_state.packing's own ITEM_CAP_CHARS still
# governs the final rendered line regardless of this per-field cap.
MAX_SUMMARY_OPTIONS = 16  # Matches brood_chamber.questions.MAX_OPTIONS's own scale.
SUMMARY_OPTION_CAP_CHARS = 200  # Matches brood_chamber.questions.MAX_OPTION_CHARS's own scale.
MAX_PRINCIPAL_ID_CHARS = 128  # A bee id or role name.
MAX_PRINCIPAL_ROLE_CHARS = 64

__all__ = [
    "MAX_SUMMARY_OPTIONS",
    "SUMMARY_OPTION_CAP_CHARS",
    "SUMMARY_TEXT_CAP_CHARS",
    "SUMMARY_TITLE_CAP_CHARS",
    "AlarmSummary",
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

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """Return up to `limit` recent decisions, as flat summaries."""
        ...

    async def pins(self) -> tuple[Pin, ...]:
        """Return every pin visible to this source, from the memory store."""
        ...

    async def notes(self) -> tuple[Note, ...]:
        """Return every note visible to this source, from the memory store."""
        ...
