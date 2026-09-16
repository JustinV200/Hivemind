"""Define AssembleRequest, Prompt and assemble: pack hot state into a token-budgeted prompt.

`assemble` is the core of codingrules section 8.9's memory model: "What a model sees is assembled
per episode from the tiers below; nothing accumulates." It filters every candidate item (pins,
notes, tasks, Alarms, questions, decisions, Cell Wax) by the principal's clearance allowance
first, then packs by relevance (`hivemind.memory.relevance.score`, roadmap step 4.1: recency
decay, task linkage, Alarm/Cell-Wax severity, and a pin's own non-decaying floor) -- highest score
first, in one pass, stopping
the moment the running total would exceed the budget, so whatever is left over is by construction
the lowest-scored candidates (codingrules section 8.9: "on overflow the lowest-scored items are
dropped first"). Any item whose rendered text is longer than `request.budget.item_cap_chars` is
replaced by a short reference before it is even token-counted, so one huge tool result never crowds
out everything else in hot state ("a large tool result is stored as Nectar with a reference in hot
state, never inlined" -- until the Honey Store exists in phase 7, that reference is a
`hivemind.memory.bee_bread.deposit.deposit_tool_result` entry id, deposited by a caller, never by
this module itself, which stays pure). The result's `sections` are keyed only `PINS` and
`HOT_STATE` (never `RETRIEVED`, which stays empty in memory v0), ready to hand straight to
`hivemind.llm.prompts.render`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.awake and wardens.awake
    to build the prompt for an awake episode. Calls into hivemind.llm (for SectionLabel, to key its
    result the way render() expects), hivemind.memory.counter (TokenCounter), hivemind.memory.
    hot_state.summaries, hivemind.memory.notes, hivemind.memory.pins and hivemind.memory.relevance
    (RelevanceScore, Scorable, item_id, score) only.

Key invariants:
    - Every candidate is filtered by `item.clearance.rank <= principal.clearance.rank` before
      ranking or packing begins; a C2 item is never scored, rendered or counted for a C1 (or
      lower) principal (codingrules section 8.9).
    - Packing is a single pass in relevance order (highest first): once an item does not fit the
      remaining budget, every item after it in that same pass is dropped too, never
      skipped-and-retried against a smaller later item. A pin's non-decaying score floor
      (`hivemind.memory.relevance.PIN_FLOOR`) means every pin sorts before every non-pin, so this
      matches pins' old "never dropped except for its own size" behaviour in every realistic
      budget (an oversized pin is already replaced by a small reference before it is counted).
    - `assemble` is pure apart from its three injected effects, `sources`, `counter` and (when
      `request.now` is unset) the wall clock: given the same request, sources and counter answers,
      it always packs the same Prompt.

See Also:
    - .claude/codingrules.md section 8.9 for the packing, budget and clearance rules this module
      implements.
    - .claude/roadmap.md step 4.1 for the relevance-ordered packing and per-item-cap requirements.
    - hivemind.memory.relevance for score, RelevanceScore, Scorable and item_id, this module's
      ranking half.
    - hivemind.memory.counter for TokenCounter, the injected effect this module counts through.
    - hivemind.memory.hot_state.summaries for the summary models, HotStateSources and TokenBudget
      this module reads.
    - hivemind.llm.prompts for render, the function a caller passes `Prompt.sections` to.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.llm import SectionLabel
from hivemind.memory.counter import TokenCounter
from hivemind.memory.hot_state.summaries import (
    ITEM_CAP_CHARS,
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    HotStateSources,
    Principal,
    QuestionSummary,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
)
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.relevance import RelevanceScore, Scorable, item_id, score
from waggle.ids import CellId, TaskId
from waggle.messages.base import UtcDatetime

RECENT_DECISIONS_LIMIT = 20  # Generous default; packing still drops whichever ones do not fit.

# Pins sort before every category at equal relevance score (PIN_FLOOR already guarantees that in
# practice); the rest follow codingrules section 8.9's old tie-break order. Only ever breaks a tie
# between two items with the exact same RelevanceScore.value, which real recency decay makes rare.
_CATEGORY_RANK: dict[type, int] = {
    Pin: -1,
    AlarmSummary: 0,
    TaskSummary: 1,
    QuestionSummary: 2,
    DecisionSummary: 3,
    Note: 4,
    CellWaxSummary: 5,
}

__all__ = ["ITEM_CAP_CHARS", "RECENT_DECISIONS_LIMIT", "AssembleRequest", "Prompt", "assemble"]


class AssembleRequest(BaseModel):
    """Everything `assemble` needs about the reader, the trigger and the budget for one episode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal: Principal = Field(description="Who this prompt is being assembled for.")
    event: TriggerEvent = Field(description="What triggered this episode.")
    budget: TokenBudget = Field(description="How much room the assembled sections have.")
    system_hint: str | None = Field(
        default=None,
        description="Extra guidance folded into the triggering event's own text, when set.",
    )
    now: UtcDatetime | None = Field(
        default=None,
        description="Reference time relevance decay is scored against; the wall clock at call "
        "time when unset (existing callers that predate roadmap step 4.1 never set this).",
    )
    cells_in_play: frozenset[CellId] = Field(
        default_factory=frozenset,
        description="Cells this episode is currently a placement or assignment candidate for "
        "(roadmap step 4.2a). Cell Wax for a Cell not in this set is never even fetched as a "
        "candidate (hivemind.memory.hot_state.summaries.HotStateSources.wax); empty means no "
        "Cell's wax appears in this prompt at all.",
    )


class Prompt(BaseModel):
    """The packed result of one `assemble` call, ready for `hivemind.llm.prompts.render`.

    Carries the labelled sections themselves, the triggering event's own text, and a record of
    which items were packed in and which were left out.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: dict[SectionLabel, str] = Field(
        default_factory=dict, description="PINS and/or HOT_STATE text, keyed for render()."
    )
    event_text: str = Field(description="The triggering event's text, becoming the EVENT section.")
    token_count: int = Field(ge=0, description="Tokens spent on sections plus event_text.")
    included: tuple[str, ...] = Field(default=(), description="Ids of every item packed in.")
    dropped: tuple[str, ...] = Field(default=(), description="Ids of every candidate left out.")


@dataclass
class _PackResult:
    """Items packed so far (with rendered text), which ids dropped, and the running token total."""

    included: list[tuple[Scorable, str]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    total_tokens: int = 0


async def assemble(
    request: AssembleRequest, sources: HotStateSources, counter: TokenCounter
) -> Prompt:
    """Pack hot state into a token-budgeted Prompt for one episode.

    Args:
        request: The principal, trigger, budget and (optionally) reference time this prompt is for.
        sources: Where every candidate item comes from.
        counter: How each candidate's rendered text is token-counted.

    Returns:
        A Prompt whose `sections` carries at most PINS and HOT_STATE, packed to
        `request.budget.max_input_tokens - request.budget.output_reserve`.
    """
    allowance = request.principal.clearance
    target_tokens = request.budget.max_input_tokens - request.budget.output_reserve
    now = request.now if request.now is not None else datetime.now(UTC)

    candidates = await _gather_candidates(sources, allowance, request.cells_in_play)
    active_tasks = frozenset(item.id for item in candidates if isinstance(item, TaskSummary))
    pin_ids = frozenset(item_id(item) for item in candidates if isinstance(item, Pin))

    ranked = _rank(candidates, now, active_tasks, pin_ids)
    packed = await _pack(ranked, target_tokens, request.budget.item_cap_chars, counter)

    event_text = _event_text(request)
    event_tokens = await counter.count(event_text)
    return Prompt(
        sections=_render_sections(packed.included),
        event_text=event_text,
        token_count=packed.total_tokens + event_tokens,
        included=tuple(item_id(item) for item, _text in packed.included),
        dropped=tuple(packed.dropped),
    )


async def _gather_candidates(
    sources: HotStateSources, allowance: HoneyClearance, cells_in_play: frozenset[CellId]
) -> list[Scorable]:
    """Fetch every candidate (pins included) and filter by clearance; unsorted.

    `sources.wax(cells_in_play)` is always called, even with an empty set: its own contract
    (`HotStateSources.wax`) is to return nothing for no Cells, so an empty `cells_in_play` still
    yields an empty `wax` tuple rather than needing a special case here.
    """
    tasks = await sources.active_tasks()
    alarms = await sources.open_alarms()
    questions = await sources.pending_questions()
    decisions = await sources.recent_decisions(RECENT_DECISIONS_LIMIT)
    notes = await sources.notes()
    pins = await sources.pins()
    wax = await sources.wax(cells_in_play)
    # Name the pool's union type explicitly: mypy widens a splat of seven different tuple types to
    # BaseModel otherwise, losing the `clearance` every candidate carries.
    pool: tuple[Scorable, ...] = (*pins, *tasks, *alarms, *questions, *decisions, *notes, *wax)
    return [item for item in pool if item.clearance.rank <= allowance.rank]


def _rank(
    candidates: Sequence[Scorable],
    now: datetime,
    active_tasks: frozenset[TaskId],
    pin_ids: frozenset[str],
) -> list[Scorable]:
    """Score every candidate and return them highest-relevance first."""
    scored = [(item, score(item, now, active_tasks, pin_ids)) for item in candidates]
    scored.sort(key=_sort_key)
    return [item for item, _relevance in scored]


async def _pack(
    ranked: Sequence[Scorable], target_tokens: int, item_cap: int, counter: TokenCounter
) -> _PackResult:
    """Pack `ranked` (highest score first) until the budget fills, then drop the rest."""
    result = _PackResult()
    stopped = False
    for item in ranked:
        iid = item_id(item)
        if stopped:
            result.dropped.append(iid)
            continue
        text, tokens = await _sized_text(iid, _raw_text(item), item_cap, counter)
        if result.total_tokens + tokens > target_tokens:
            stopped = True  # Packing stops here: everything after this item is dropped too.
            result.dropped.append(iid)
            continue
        result.included.append((item, text))
        result.total_tokens += tokens
    return result


async def _sized_text(
    iid: str, raw_text: str, item_cap: int, counter: TokenCounter
) -> tuple[str, int]:
    """Return `raw_text` (or a reference to it, if oversized) and its token count."""
    text = raw_text if len(raw_text) <= item_cap else _reference(iid, raw_text)
    return text, await counter.count(text)


def _reference(iid: str, raw_text: str) -> str:
    """Build the one-line reference an oversized item is replaced with."""
    return f"[ref {iid}: {len(raw_text)} chars, fetch by id]"


def _render_sections(included: Sequence[tuple[Scorable, str]]) -> dict[SectionLabel, str]:
    """Split packed (item, text) pairs back into PINS and HOT_STATE, preserving pack order."""
    pin_lines = [text for item, text in included if isinstance(item, Pin)]
    hot_lines = [text for item, text in included if not isinstance(item, Pin)]
    sections: dict[SectionLabel, str] = {}
    if pin_lines:
        sections[SectionLabel.PINS] = "\n".join(pin_lines)
    if hot_lines:
        sections[SectionLabel.HOT_STATE] = "\n".join(hot_lines)
    return sections


def _event_text(request: AssembleRequest) -> str:
    """Fold `system_hint` (when set) onto the triggering event's own summary text."""
    if request.system_hint is None:
        return request.event.summary
    return f"{request.system_hint}\n\n{request.event.summary}"


def _raw_text(item: Scorable) -> str:
    """Return `item`'s un-capped rendered text: a Pin's own fact, or a rendered hot-state line."""
    if isinstance(item, Pin):
        return item.text
    return _render_line(item)


def _sort_key(entry: tuple[Scorable, RelevanceScore]) -> tuple[float, int, str]:
    """Sort by score descending; break a tie by category then id (rarely reached, see PIN_FLOOR)."""
    item, relevance = entry
    return (-relevance.value, _CATEGORY_RANK[type(item)], item_id(item))


def _render_line(
    item: TaskSummary | AlarmSummary | QuestionSummary | DecisionSummary | CellWaxSummary | Note,
) -> str:
    """Render one non-pin hot-state item to a single labelled line of plain text."""
    if isinstance(item, TaskSummary):
        return f"Task {item.id} [{item.status}] {item.title}: {item.objective}"
    if isinstance(item, AlarmSummary):
        task = f" task={item.task_id}" if item.task_id is not None else ""
        return f"Alarm {item.id} [{item.severity}/{item.kind}]{task}: {item.detail}"
    if isinstance(item, QuestionSummary):
        options = f" options={list(item.options)}" if item.options else ""
        return f"Question {item.id} (task {item.task_id}){options}: {item.text}"
    if isinstance(item, DecisionSummary):
        at = item.at.isoformat()
        return f"Decision {item.episode_id} at {at}: {item.decision} -> {item.action}"
    if isinstance(item, CellWaxSummary):
        return f"Cell Wax {item.id} [{item.severity}] cell={item.cell_id}: {item.text}"
    return f"Note by {item.author}: {item.text}"
