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
state, never inlined": the reference names the item's own id, which a caller has already
deposited, e.g. through `hivemind.memory.bee_bread.deposit.deposit_tool_result` -- never this
module itself, which stays pure). Once hot state is packed, the cold tier follows: the Honey hits
the caller retrieved and scanned for this episode (`AssembleRequest.retrieved`, `RetrievedItem`s;
Honey is the Hive's ripened, labelled knowledge) are packed by `hivemind.memory.hot_state.
retrieved.pack_retrieved` into whatever room hot state left, capped at the budget's own retrieved
share, and become the `RETRIEVED` section. The result's `sections` are keyed `PINS`, `HOT_STATE`
and `RETRIEVED` (each only when it has content), ready to hand straight to
`hivemind.llm.prompts.render`, which delimits and labels each one. A resumed
`hivemind.memory.Handoff` (`HotStateSources.handoff`) is rendered unconditionally into its own
delimited block inside `HOT_STATE` (`_render_handoff`) -- never scored or dropped for budget the
way the candidates above are, only bounded by
`item_cap_chars` -- because what a prior attempt already did and must not repeat is safety-critical
context a resuming bee cannot afford to lose to relevance ranking (defect 2 this dispatch fixes:
before this, only a resumed Handoff's `decisions` reached the model at all, through
`recent_decisions`). Roadmap steps 10.6b and 10.6d add two refusals and one rendering rule: a
tainted item (a decision from a tainted record, a tainted resumed Handoff, a tainted retrieved item)
is refused outright, whatever its score, and its id recorded in `Prompt.refused`; the resumed
Handoff is filtered by the reader's clearance like every other candidate (it used to bypass that
filter); and outside text (the trigger's `untrusted` words, each retrieved item) is rendered under
its scan verdict by `hivemind.memory.hot_state.untrusted.render_untrusted`: fenced as data,
labelled harder, or withheld.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.awake and wardens.awake
    to build the prompt for an awake episode. Calls into hivemind.llm (for SectionLabel, to key its
    result the way render() expects), hivemind.memory.counter (TokenCounter), hivemind.memory.
    handoff (Handoff, for the resumed-Handoff block), hivemind.memory.hot_state.summaries,
    hivemind.memory.hot_state.retrieved (the cold tier's packing), hivemind.memory.hot_state.
    untrusted (RetrievedItem, render_untrusted), hivemind.memory.notes, hivemind.memory.pins,
    hivemind.memory.relevance (RelevanceScore, Scorable, item_id, score), hivemind.memory.taint
    and waggle only; never hivemind.honey_store, whose hits arrive as scanned data.

Key invariants:
    - Every candidate is filtered by `item.clearance.rank <= principal.clearance.rank` before
      ranking or packing begins; a C2 item is never scored, rendered or counted for a C1 (or
      lower) principal (codingrules section 8.9). Retrieved items get the same filter, as defence
      in depth, even though the Queen already filtered them by the reader's ceiling.
    - Hot state packs first, exactly as if nothing were retrieved: no hit ever takes room from hot
      state. Hits get only what hot state (and a resumed Handoff) left, capped at
      `TokenBudget.retrieved_fraction` of the packing target, so the RETRIEVED section never
      exceeds its own share and the packed sections never exceed the target because of it.
    - Packing is a single pass in relevance order (highest first): once an item does not fit the
      remaining budget, every item after it in that same pass is dropped too, never
      skipped-and-retried against a smaller later item. A pin's non-decaying score floor
      (`hivemind.memory.relevance.PIN_FLOOR`) means every pin sorts before every non-pin, so this
      matches pins' old "never dropped except for its own size" behaviour in every realistic
      budget (an oversized pin is already replaced by a small reference before it is counted).
    - `assemble` is pure apart from its four injected effects, `sources`, `counter`, `on_drop` and
      (when `request.now` is unset) the wall clock: given the same request, sources and counter
      answers, it always packs the same Prompt. `on_drop` (roadmap step 4.4) is a synchronous
      callback invoked once per dropped hot-state candidate, never for a retrieved item (nothing
      to archive: it already lives in the Honey Store); unset, it changes nothing.
    - A TAINTED item never reaches a Prompt, and a resumed Handoff above the reader's clearance
      never does either (roadmap 10.6d); neither is passed to `on_drop`, since neither was a
      budget drop and both are already stored.
    - A resumed Handoff's own `do_not_redo` and `next_steps` render as explicit instruction lists
      ("Already done, do not repeat: ...", "Next: ...") and `pinned_facts` renders verbatim,
      never re-summarised (codingrules section 8.9: "compaction... copies pins verbatim").

See Also:
    - .claude/codingrules.md section 8.9 for the packing, budget and clearance rules this module
      implements.
    - .claude/roadmap.md step 4.1 for the relevance-ordered packing and per-item-cap requirements.
    - hivemind.memory.relevance for score, RelevanceScore, Scorable and item_id, this module's
      ranking half.
    - hivemind.memory.counter for TokenCounter, the injected effect this module counts through.
    - hivemind.memory.handoff for Handoff, the resumed-Handoff shape `_render_handoff` renders.
    - hivemind.memory.hot_state.summaries for the summary models, HotStateSources and TokenBudget
      this module reads.
    - hivemind.memory.hot_state.retrieved for pack_retrieved, the cold tier's packing step.
    - hivemind.llm.prompts for render, the function a caller passes `Prompt.sections` to.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.llm import SectionLabel
from hivemind.memory.counter import TokenCounter
from hivemind.memory.handoff import Handoff
from hivemind.memory.hot_state.retrieved import pack_retrieved, retrieved_share
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
from hivemind.memory.hot_state.untrusted import RetrievedItem, render_untrusted
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.relevance import RelevanceScore, Scorable, item_id, score
from hivemind.memory.taint.marker import is_refused
from waggle.ids import CellId, TaskId
from waggle.messages.base import UtcDatetime

RECENT_DECISIONS_LIMIT = 20  # Generous default; packing still drops whichever ones do not fit.
# A resumed Handoff's own list fields (do_not_redo, next_steps, ...) can hold up to Handoff's own
# MAX_LIST_ITEMS (64, a storage cap); a prompt section only needs enough for a resuming bee to act
# on, so this is a separate, much smaller render budget -- oversized lists are truncated with a
# count (module docstring's own "Key invariants" on the handoff section, added below).
MAX_HANDOFF_LIST_ITEMS_SHOWN = 10
# The id a refused resumed Handoff is recorded under in Prompt.refused: the Handoff itself carries
# no id (its key is the HandoffRef the resuming runtime holds), and there is only ever one.
RESUMED_HANDOFF_ID = "resumed-handoff"

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

__all__ = [
    "ITEM_CAP_CHARS",
    "MAX_HANDOFF_LIST_ITEMS_SHOWN",
    "RECENT_DECISIONS_LIMIT",
    "RESUMED_HANDOFF_ID",
    "AssembleRequest",
    "Prompt",
    "assemble",
]


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
    retrieved: tuple[RetrievedItem, ...] = Field(
        default=(),
        description="The Honey hits (and any Nectar) the caller retrieved for this episode, each "
        "already scanned and carrying its taint label (the cold tier, roadmaps 7.7 and 10.6b), "
        "e.g. a Drone's TaskAssign.honey after its scan. Untrusted reference data: packed after "
        "hot state into the RETRIEVED section, best score first, within "
        "budget.retrieved_fraction, each under its verdict; one labelled above the principal's "
        "clearance is dropped unseen, a TAINTED one refused.",
    )


class Prompt(BaseModel):
    """The packed result of one `assemble` call, ready for `hivemind.llm.prompts.render`.

    Carries the labelled sections themselves, the triggering event's own text, and a record of
    which items were packed in and which were left out.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: dict[SectionLabel, str] = Field(
        default_factory=dict,
        description="PINS, HOT_STATE and/or RETRIEVED text, keyed for render(); a section with "
        "nothing packed into it is absent.",
    )
    event_text: str = Field(description="The triggering event's text, becoming the EVENT section.")
    token_count: int = Field(ge=0, description="Tokens spent on sections plus event_text.")
    included: tuple[str, ...] = Field(
        default=(),
        description="Ids of every item packed in: hot-state ids, then `honey:<honey_ref>` (or "
        "`nectar:<id>`) per retrieved item.",
    )
    dropped: tuple[str, ...] = Field(
        default=(),
        description="Ids of every candidate left out for the budget, in the same two forms; a "
        "candidate filtered out by clearance is in neither list.",
    )
    refused: tuple[str, ...] = Field(
        default=(),
        description="Ids of every item refused outright for its taint label (roadmap 10.6d), and "
        "RESUMED_HANDOFF_ID for a resumed Handoff refused for its taint or its clearance.",
    )


@dataclass
class _PackResult:
    """Items packed so far (with rendered text), which ids dropped, and the running token total."""

    included: list[tuple[Scorable, str]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    total_tokens: int = 0


async def assemble(
    request: AssembleRequest,
    sources: HotStateSources,
    counter: TokenCounter,
    on_drop: Callable[[Scorable], None] | None = None,
) -> Prompt:
    """Pack hot state, then the retrieved Honey hits, into a token-budgeted Prompt for one episode.

    Args:
        request: The principal, trigger, budget, retrieved hits and (optionally) reference time
            this prompt is for.
        sources: Where every hot-state candidate comes from.
        counter: How each candidate's and hit's rendered text is token-counted.
        on_drop: Called once, synchronously, for every hot-state candidate left out of the budget
            (roadmap step 4.4: "the packer must report drops"), never for a retrieved hit.
            `Prompt.dropped` carries every id either way; this is for a caller that needs the
            item itself, e.g. to archive it into Bee Bread (`hivemind.memory.bee_bread.deposit.
            deposit_dropped_items`). Unset (the default) costs nothing extra.

    Returns:
        A Prompt whose `sections` carries at most PINS, HOT_STATE and RETRIEVED, packed to
        `request.budget.max_input_tokens - request.budget.output_reserve`.
    """
    target_tokens = request.budget.max_input_tokens - request.budget.output_reserve
    packed, refused = await _pack_hot_state(request, sources, counter, on_drop, target_tokens)
    handoff = await _handoff_section(sources, request, counter)

    # The cold tier packs last, into what hot state left, never past its own share of the target.
    room = min(
        target_tokens - packed.total_tokens - handoff.tokens, retrieved_share(request.budget)
    )
    retrieved = await pack_retrieved(
        request.retrieved, request.principal.clearance, room, request.budget.item_cap_chars, counter
    )

    event_text = _event_text(request)
    event_tokens = await counter.count(event_text)
    return Prompt(
        sections=_render_sections(packed.included, handoff.text, retrieved.text),
        event_text=event_text,
        token_count=packed.total_tokens + handoff.tokens + retrieved.tokens + event_tokens,
        included=(*(item_id(item) for item, _text in packed.included), *retrieved.included),
        dropped=(*packed.dropped, *retrieved.dropped),
        refused=(*refused, *handoff.refused, *retrieved.refused),
    )


async def _pack_hot_state(
    request: AssembleRequest,
    sources: HotStateSources,
    counter: TokenCounter,
    on_drop: Callable[[Scorable], None] | None,
    target_tokens: int,
) -> tuple[_PackResult, tuple[str, ...]]:
    """Gather, clearance- and taint-filter, rank and pack every hot-state candidate.

    Returns the packing and the ids of every candidate refused for its taint label.
    """
    now = request.now if request.now is not None else datetime.now(UTC)
    candidates, refused = await _gather_candidates(
        sources, request.principal.clearance, request.cells_in_play
    )
    active_tasks = frozenset(item.id for item in candidates if isinstance(item, TaskSummary))
    pin_ids = frozenset(item_id(item) for item in candidates if isinstance(item, Pin))
    ranked = _rank(candidates, now, active_tasks, pin_ids)
    packed = await _pack(ranked, target_tokens, request.budget.item_cap_chars, counter, on_drop)
    return packed, refused


@dataclass(frozen=True, slots=True)
class _HandoffSection:
    """A resumed Handoff's rendered block, its token count, and its id if it was refused."""

    text: str = ""  # "" when there is no Handoff, or it was refused.
    tokens: int = 0
    refused: tuple[str, ...] = ()  # (RESUMED_HANDOFF_ID,) when it was refused.


async def _handoff_section(
    sources: HotStateSources, request: AssembleRequest, counter: TokenCounter
) -> _HandoffSection:
    """Fetch, check, render and token-count a resumed Handoff; empty when there is none.

    Defect 2 (this dispatch's own report): a resumed Handoff used to reach a resuming bee's
    prompt only through `recent_decisions`; every other field (`do_not_redo`, `next_steps`, ...)
    never appeared at all. Rendered unconditionally into its own delimited block inside
    `HOT_STATE` -- never scored or dropped for budget, only bounded by `item_cap_chars` -- because
    what a prior attempt already did and must not be redone is safety-critical, not a
    relevance-ranked nice-to-have. Roadmap 10.6d: unconditional stops at the two refusals every
    other item meets, its clearance above the reader's and its taint label, which it used to
    bypass.
    """
    handoff = await sources.handoff()
    if handoff is None:
        return _HandoffSection()
    over_cleared = handoff.clearance.rank > request.principal.clearance.rank
    if over_cleared or is_refused(handoff.tainted):
        return _HandoffSection(refused=(RESUMED_HANDOFF_ID,))
    text = _render_handoff(handoff, request.budget.item_cap_chars)
    return _HandoffSection(text=text, tokens=await counter.count(text))


async def _gather_candidates(
    sources: HotStateSources, allowance: HoneyClearance, cells_in_play: frozenset[CellId]
) -> tuple[list[Scorable], tuple[str, ...]]:
    """Fetch every candidate (pins included), filter by clearance and taint; unsorted.

    `sources.wax(cells_in_play)` is always called, even with an empty set: its own contract
    (`HotStateSources.wax`) is to return nothing for no Cells, so an empty `cells_in_play` still
    yields an empty `wax` tuple rather than needing a special case here. Returns the survivors
    and the ids of every candidate refused for its taint label (roadmap 10.6d).
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
    visible = [item for item in pool if item.clearance.rank <= allowance.rank]
    # A decision carries the taint label of the record it came from; a TAINTED one is refused
    # outright, before scoring, so no relevance can ever pack it (roadmap 10.6d).
    refused = tuple(item_id(item) for item in visible if _is_tainted(item))
    return [item for item in visible if not _is_tainted(item)], refused


def _is_tainted(item: Scorable) -> bool:
    """Return whether a candidate carries a TAINTED label (only decisions can carry one)."""
    return isinstance(item, DecisionSummary) and is_refused(item.tainted)


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
    ranked: Sequence[Scorable],
    target_tokens: int,
    item_cap: int,
    counter: TokenCounter,
    on_drop: Callable[[Scorable], None] | None,
) -> _PackResult:
    """Pack `ranked` (highest score first) until the budget fills, then drop the rest."""
    result = _PackResult()
    stopped = False
    for item in ranked:
        iid = item_id(item)
        if stopped:
            result.dropped.append(iid)
            if on_drop is not None:
                on_drop(item)
            continue
        text, tokens = await _sized_text(iid, _raw_text(item), item_cap, counter)
        if result.total_tokens + tokens > target_tokens:
            stopped = True  # Packing stops here: everything after this item is dropped too.
            result.dropped.append(iid)
            if on_drop is not None:
                on_drop(item)
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


def _render_sections(
    included: Sequence[tuple[Scorable, str]], handoff_text: str, retrieved_text: str
) -> dict[SectionLabel, str]:
    """Split packed (item, text) pairs into PINS and HOT_STATE in pack order; add RETRIEVED.

    Args:
        included: Every scored candidate `_pack` kept, with its own rendered text.
        handoff_text: The resumed Handoff's own rendered block (`_render_handoff`), or "" when
            this episode is not resuming one; appended after the scored hot-state lines so it
            reads after pins and hot state, before the event (codingrules 8.9's "stable prefix").
        retrieved_text: The packed cold tier (`pack_retrieved`'s text), or "" when no item was
            packed; it becomes the RETRIEVED section, which render() places after hot state.
    """
    pin_lines = [text for item, text in included if isinstance(item, Pin)]
    hot_lines = [text for item, text in included if not isinstance(item, Pin)]
    if handoff_text:
        hot_lines.append(handoff_text)
    sections: dict[SectionLabel, str] = {}
    if pin_lines:
        sections[SectionLabel.PINS] = "\n".join(pin_lines)
    if hot_lines:
        sections[SectionLabel.HOT_STATE] = "\n".join(hot_lines)
    if retrieved_text:
        sections[SectionLabel.RETRIEVED] = retrieved_text
    return sections


def _event_text(request: AssembleRequest) -> str:
    """Build the event's text: `system_hint`, the summary, then its outside text under its verdict.

    The summary is the Hive's own framing and is shown as it is; the trigger's `untrusted` words
    (a human's chat message) are rendered by `render_untrusted`, so the scanner's verdict decides
    whether they are fenced as data, labelled harder, or withheld (roadmap 10.6b).
    """
    body = request.event.summary
    if request.event.untrusted is not None:
        body = f"{body}\n{render_untrusted(request.event.untrusted)}"
    if request.system_hint is None:
        return body
    return f"{request.system_hint}\n\n{body}"


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


def _render_handoff(handoff: Handoff | None, item_cap: int) -> str:
    """Render a resumed Handoff into its own delimited block, or "" when there is none.

    Every field renders (goal, progress, decisions already reach the model through
    `recent_decisions`/`DecisionSummary`, so only the fields defect 2's report named as missing
    are rendered here): `do_not_redo` and `next_steps` as explicit instruction lists ("Already
    done, do not repeat: ...", "Next: ..."), `pinned_facts` verbatim, the rest as labelled lists
    or lines. Bounded by `item_cap` (an oversized list is truncated with a count first; if the
    whole block is still oversized even then, it is hard-truncated with a trailing char count,
    mirroring `_reference`'s own oversized-item convention -- a Handoff cannot be replaced by a
    small id lookup the way a scored item can, since a resuming bee needs at least the truncated
    do_not_redo/goal to be safe).

    Args:
        handoff: The Handoff this episode is resuming from, or None for a fresh episode.
        item_cap: `AssembleRequest.budget.item_cap_chars`, the same per-item char cap every other
            hot-state candidate is bounded by.

    Returns:
        The delimited `<<<handoff>>> ... <<<end handoff>>>` block, or "" when `handoff` is None.
    """
    if handoff is None:
        return ""
    lines = ["<<<handoff>>>", f"Goal: {handoff.goal}", f"Progress: {handoff.progress}"]
    lines += _handoff_list("Already done, do not repeat:", handoff.do_not_redo)
    lines += _handoff_list("Next:", handoff.next_steps)
    lines += _handoff_list("Tried and failed:", handoff.tried_and_failed)
    lines += _handoff_list("Constraints discovered:", handoff.constraints)
    lines += _handoff_list("Open threads:", handoff.open_threads)
    lines += _handoff_list("Pinned facts (verbatim):", handoff.pinned_facts)
    if handoff.notes:
        lines.append(f"Notes: {handoff.notes}")
    lines.append("<<<end handoff>>>")
    text = "\n".join(lines)
    if len(text) <= item_cap:
        return text
    # Still oversized after the per-list item cap below: hard-truncate the whole block, noting
    # how many characters were cut, rather than silently overflowing item_cap (module docstring).
    cut = len(text) - item_cap
    return f"{text[:item_cap]}\n...[handoff truncated, {cut} more chars]"


def _handoff_list(label: str, items: Sequence[str]) -> list[str]:
    """Render one labelled bullet list from a Handoff field, truncated to a fixed item count.

    Args:
        label: The instruction-style header this list renders under (e.g. "Next:").
        items: The Handoff field's own tuple; already capped at the source by
            `hivemind.memory.handoff.MAX_LIST_ITEMS`, a storage cap far larger than a prompt
            section needs (module-level `MAX_HANDOFF_LIST_ITEMS_SHOWN`).

    Returns:
        [] when `items` is empty (no header for an empty list); otherwise the header, one `- `
        bullet per shown item, and a trailing "(+N more, truncated)" count when `items` held more
        than `MAX_HANDOFF_LIST_ITEMS_SHOWN`.
    """
    if not items:
        return []
    shown = items[:MAX_HANDOFF_LIST_ITEMS_SHOWN]
    lines = [label] + [f"- {item}" for item in shown]
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append(f"(+{hidden} more, truncated)")
    return lines
