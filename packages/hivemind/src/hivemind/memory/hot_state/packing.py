"""Define AssembleRequest, Prompt and assemble: pack hot state into a token-budgeted prompt.

`assemble` is the core of codingrules section 8.9's memory model: "What a model sees is assembled
per episode from the tiers below; nothing accumulates." It filters every candidate item (pins,
notes, tasks, Alarms, questions, decisions) by the principal's clearance allowance first, packs
pins into their own section (never dropped for any reason but a single pin alone exceeding the
whole budget, since pins never decay), then packs the rest by recency -- newest first, with a
fixed category order (Alarms, tasks, questions, decisions, notes) breaking an exact tie -- stopping
the moment the running total would exceed the budget. Any item whose rendered text is longer than
`ITEM_CAP_CHARS` is replaced by a short reference before it is even token-counted, so one huge tool
result never crowds out everything else in hot state (codingrules section 8.9: "a large tool
result is stored as Nectar with a reference in hot state, never inlined"). The result's `sections`
are keyed only `PINS` and `HOT_STATE` (never `RETRIEVED`, which stays empty in memory v0), ready to
hand straight to `hivemind.llm.prompts.render`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.awake and wardens.awake
    (a later roadmap step) to build the prompt for an awake episode. Calls into hivemind.llm (for
    SectionLabel, to key its result the way render() expects), hivemind.memory.counter
    (TokenCounter), hivemind.memory.hot_state.summaries and hivemind.memory.notes/pins only.

Key invariants:
    - Every candidate is filtered by `item.clearance.rank <= principal.clearance.rank` before
      packing begins; a C2 item is never scored, rendered or counted for a C1 (or lower) principal
      (codingrules section 8.9).
    - Packing is a single pass in priority order (pins, then hot-state items newest first): once
      an item does not fit the remaining budget, every item after it in that same pass is dropped
      too, never skipped-and-retried against a smaller later item.
    - `assemble` is pure apart from its two injected effects, `sources` and `counter`: given the
      same request, sources and counter answers, it always packs the same Prompt.

See Also:
    - .claude/codingrules.md section 8.9 for the packing, budget and clearance rules this module
      implements.
    - hivemind.memory.counter for TokenCounter, the injected effect this module counts through.
    - hivemind.memory.hot_state.summaries for the summary models and HotStateSources this module
      reads.
    - hivemind.llm.prompts for render, the function a caller passes `Prompt.sections` to.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import assert_never

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.llm import SectionLabel
from hivemind.memory.counter import TokenCounter
from hivemind.memory.hot_state.summaries import (
    AlarmSummary,
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

# A hot-state item longer than this becomes a one-line reference instead of being inlined, so one
# bee's huge tool result or long objective never crowds out everything else (codingrules 8.9).
# Matches the manifest's future `[memory] item_cap_chars` default (docs/manifests); AssembleRequest
# carries no such field itself (the roadmap's own compact seam omits one), so this stays a fixed
# constant here until a later phase threads a manifest override through.
ITEM_CAP_CHARS = 4_000
RECENT_DECISIONS_LIMIT = 20  # Generous default; packing still drops whichever ones do not fit.

__all__ = ["ITEM_CAP_CHARS", "RECENT_DECISIONS_LIMIT", "AssembleRequest", "Prompt", "assemble"]

# The five hot-state categories (everything but pins, which pack separately and never decay).
_HotStateItem = TaskSummary | AlarmSummary | QuestionSummary | DecisionSummary | Note

# Alarms before tasks before questions before decisions before notes, at equal recency
# (codingrules section 8.9's packing order); the tie-break only, since recency is the primary key.
_CATEGORY_RANK: dict[type, int] = {
    AlarmSummary: 0,
    TaskSummary: 1,
    QuestionSummary: 2,
    DecisionSummary: 3,
    Note: 4,
}


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
    """The lines packed so far, which ids landed where, and the running token total."""

    lines: list[str] = field(default_factory=list)
    included: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    total_tokens: int = 0


async def assemble(
    request: AssembleRequest, sources: HotStateSources, counter: TokenCounter
) -> Prompt:
    """Pack hot state into a token-budgeted Prompt for one episode.

    Args:
        request: The principal, trigger and budget this prompt is for.
        sources: Where every candidate item comes from.
        counter: How each candidate's rendered text is token-counted.

    Returns:
        A Prompt whose `sections` carries at most PINS and HOT_STATE, packed to
        `request.budget.max_input_tokens - request.budget.output_reserve`.
    """
    allowance = request.principal.clearance
    target_tokens = request.budget.max_input_tokens - request.budget.output_reserve

    pins = [pin for pin in await sources.pins() if pin.clearance.rank <= allowance.rank]
    hot_items = await _gather_hot_items(sources, allowance)

    pin_result = await _pack_pins(pins, target_tokens, counter)
    hot_result = await _pack_hot_state(hot_items, target_tokens, pin_result.total_tokens, counter)

    sections: dict[SectionLabel, str] = {}
    if pin_result.lines:
        sections[SectionLabel.PINS] = "\n".join(pin_result.lines)
    if hot_result.lines:
        sections[SectionLabel.HOT_STATE] = "\n".join(hot_result.lines)

    event_text = _event_text(request)
    event_tokens = await counter.count(event_text)

    return Prompt(
        sections=sections,
        event_text=event_text,
        token_count=hot_result.total_tokens + event_tokens,
        included=(*pin_result.included, *hot_result.included),
        dropped=(*pin_result.dropped, *hot_result.dropped),
    )


async def _gather_hot_items(
    sources: HotStateSources, allowance: HoneyClearance
) -> list[_HotStateItem]:
    """Fetch every non-pin candidate, filter by clearance, and sort by recency for packing."""
    tasks = await sources.active_tasks()
    alarms = await sources.open_alarms()
    questions = await sources.pending_questions()
    decisions = await sources.recent_decisions(RECENT_DECISIONS_LIMIT)
    notes = await sources.notes()
    # Name the pool's union type explicitly: mypy widens a splat of five different tuple
    # types to BaseModel otherwise, losing the `clearance` every summary carries.
    pool: tuple[_HotStateItem, ...] = (*tasks, *alarms, *questions, *decisions, *notes)
    candidates = [item for item in pool if item.clearance.rank <= allowance.rank]
    candidates.sort(key=_sort_key)
    return candidates


async def _pack_pins(pins: Sequence[Pin], target_tokens: int, counter: TokenCounter) -> _PackResult:
    """Pack every pin that individually fits `target_tokens`; pins never decay otherwise."""
    result = _PackResult()
    for pin in pins:
        text, tokens = await _sized_text(pin.id, pin.text, counter)
        # A pin is dropped only when it alone could never fit the whole budget -- never because
        # earlier pins already used up room (codingrules section 8.9: pins never decay).
        if tokens > target_tokens:
            result.dropped.append(pin.id)
            continue
        result.lines.append(text)
        result.included.append(pin.id)
        result.total_tokens += tokens
    return result


async def _pack_hot_state(
    items: Sequence[_HotStateItem], target_tokens: int, running_total: int, counter: TokenCounter
) -> _PackResult:
    """Pack `items` (already recency-sorted) until the budget fills, then drop the rest."""
    result = _PackResult(total_tokens=running_total)
    stopped = False
    for item in items:
        item_id = _item_id(item)
        if stopped:
            result.dropped.append(item_id)
            continue
        text, tokens = await _sized_text(item_id, _render_line(item), counter)
        if result.total_tokens + tokens > target_tokens:
            stopped = True  # Packing stops here: everything after this item is dropped too.
            result.dropped.append(item_id)
            continue
        result.lines.append(text)
        result.included.append(item_id)
        result.total_tokens += tokens
    return result


async def _sized_text(item_id: str, raw_text: str, counter: TokenCounter) -> tuple[str, int]:
    """Return `raw_text` (or a reference to it, if oversized) and its token count."""
    text = raw_text if len(raw_text) <= ITEM_CAP_CHARS else _reference(item_id, raw_text)
    return text, await counter.count(text)


def _reference(item_id: str, raw_text: str) -> str:
    """Build the one-line reference an oversized item is replaced with."""
    return f"[ref {item_id}: {len(raw_text)} chars, fetch by id]"


def _event_text(request: AssembleRequest) -> str:
    """Fold `system_hint` (when set) onto the triggering event's own summary text."""
    if request.system_hint is None:
        return request.event.summary
    return f"{request.system_hint}\n\n{request.event.summary}"


def _item_id(item: _HotStateItem) -> str:
    """Return the id field packing tracks `item` by; DecisionSummary's is named differently."""
    match item:
        case DecisionSummary():
            return item.episode_id
        case TaskSummary() | AlarmSummary() | QuestionSummary() | Note():
            return item.id
        case _ as unreachable:
            assert_never(unreachable)


def _recency_at(item: _HotStateItem) -> datetime:
    """Return the timestamp packing sorts `item` by, newest first."""
    match item:
        case TaskSummary():
            return item.updated_at
        case AlarmSummary():
            return item.raised_at
        case QuestionSummary():
            return item.asked_at
        case DecisionSummary():
            return item.at
        case Note():
            return item.written_at
        case _ as unreachable:
            assert_never(unreachable)


def _sort_key(item: _HotStateItem) -> tuple[float, int]:
    """Sort newest first; break an exact tie by _CATEGORY_RANK (codingrules section 8.9)."""
    return (-_recency_at(item).timestamp(), _CATEGORY_RANK[type(item)])


def _render_line(item: _HotStateItem) -> str:
    """Render one hot-state item to a single labelled line of plain text."""
    match item:
        case TaskSummary():
            return f"Task {item.id} [{item.status}] {item.title}: {item.objective}"
        case AlarmSummary():
            task = f" task={item.task_id}" if item.task_id is not None else ""
            return f"Alarm {item.id} [{item.severity}/{item.kind}]{task}: {item.detail}"
        case QuestionSummary():
            options = f" options={list(item.options)}" if item.options else ""
            return f"Question {item.id} (task {item.task_id}){options}: {item.text}"
        case DecisionSummary():
            at = item.at.isoformat()
            return f"Decision {item.episode_id} at {at}: {item.decision} -> {item.action}"
        case Note():
            return f"Note by {item.author}: {item.text}"
        case _ as unreachable:
            assert_never(unreachable)
