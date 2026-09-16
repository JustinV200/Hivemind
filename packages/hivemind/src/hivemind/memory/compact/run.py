"""Implement compact: summarise Bee Bread source records on ModelSlot.RIPENER (roadmap step 4.3).

Compaction is the last step of the House Bee (a maintenance role, `hivemind.workers.roles.
house_bee`) sweep duty: once demotion (`hivemind.memory.demote`) has moved stale items out of hot
state and into Bee Bread (the warm memory tier), `compact` folds a batch of those Bee Bread entries
into one new `SUMMARY` entry, so the tier does not grow without bound. Docs/adr/0022 fixes the shape
this module makes real: summarise only from source records, **never** from a previous summary (so
drift is bounded to one hop -- `_validate_request` below enforces it by raising
`SummaryOfSummaryError`), copy every pin verbatim rather than letting the model paraphrase a fact
that is supposed to never change, and run the model call itself on `ModelSlot.RIPENER` (batch work,
low grade -- the manifest binds it separately from `WORKER` so compaction never competes with a
bee's own work for the same seats). The prompt is assembled fresh from `request.sources` on every
call (never accumulated, codingrules section 8.8) through `hivemind.llm.prompts.render` and handed
to `hivemind.llm.complete_structured`, which degrades rungs and bindings the same way every other
structured call in the Hive does (codingrules section 8.6).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.compact`. Called
    by `hivemind.workers.roles.house_bee.sweep.run_sweep`'s compaction phase (roadmap step 4.3, a
    sibling dispatch). Calls into hivemind.cell (HoneyClearance), hivemind.llm (BoundModel,
    LLMRequest, Message, PromptName, Role, SectionLabel, complete_structured, render),
    hivemind.memory.bee_bread.entry, hivemind.memory.compact.schema, hivemind.memory.context,
    hivemind.memory.counter, hivemind.memory.errors, hivemind.memory.pins, hivemind.pheromone
    (MemoryEvent) and waggle only.

Key invariants:
    - `compact` never summarises a source whose own `kind` is `BeeBreadEntryKind.SUMMARY`
      (`SummaryOfSummaryError`); the resulting entry's own `kind` is always `SUMMARY`, so a second
      compaction pass over it is refused the same way -- one level, by construction.
    - The pins in `request.pins` reach the stored entry's text byte-for-byte, appended after the
      model's own summary; the model is never shown them, so it cannot paraphrase or drop one.
    - The stored entry's clearance is the highest clearance among `request.sources` **and**
      `request.pins` (the deviation this dispatch's report calls out: the roadmap step's own
      wording names only "sources", but a pin's verbatim text is part of the entry too, and
      labelling it below its own clearance would be a clearance leak, not a simplification).
    - The `memory.compacted` trail event commits in the same store call as the new entry
      (`hivemind.memory.store.protocol.MemoryStore.add_bee_bread_entry`), matching every other
      deposit in this package (codingrules section 12).

See Also:
    - docs/adr/0022-memory-tiers-relevance-and-compaction.md for the decision this module makes
      real.
    - .claude/roadmap.md step 4.3 for this module's field-by-field spec verbatim.
    - hivemind.memory.demote for should_demote/demote, the step that fills Bee Bread this module
      later compacts.
    - hivemind.memory.bee_bread for BeeBreadEntry, BeeBreadEntryKind and BeeBread, the lookup
      surface a caller uses to gather `request.sources`.
    - hivemind.llm.ladders.structured for complete_structured, the degradation ladder this module
      calls through rather than a provider directly.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.llm import (
    BoundModel,
    LLMRequest,
    Message,
    PromptName,
    Role,
    SectionLabel,
    complete_structured,
    render,
)
from hivemind.memory.bee_bread.entry import MAX_REF_IDS, BeeBreadEntry, BeeBreadEntryKind
from hivemind.memory.compact.schema import (
    CompactionDeps,
    CompactionRequest,
    CompactionResult,
    CompactionSchema,
)
from hivemind.memory.context import MemoryContext
from hivemind.memory.counter import EstimateCounter, ProviderCounter, TokenCounter
from hivemind.memory.errors import (
    ClearanceError,
    EmptyCompactionError,
    SummaryOfSummaryError,
    TooManySourcesError,
)
from hivemind.memory.pins import Pin
from hivemind.pheromone import MemoryEvent
from waggle.ids import new_event_id

# The output budget for the one structured call this module makes; generous enough for
# CompactionSchema's own fields plus the ladder's own PROMPTED-rung JSON preamble, small because a
# summary is short by design.
RIPENER_OUTPUT_TOKENS = 2_048
_ONE_LINE_INSTRUCTION = (
    "Summarise the source records shown above into one summary: a short prose account, a bounded "
    "list of key facts, and a bounded list of open threads."
)

__all__ = ["RIPENER_OUTPUT_TOKENS", "compact"]


async def compact(request: CompactionRequest, deps: CompactionDeps) -> CompactionResult:
    """Summarise `request.sources` on `ModelSlot.RIPENER` and deposit the result into Bee Bread.

    Args:
        request: The source records, the pins to copy verbatim, and the caller's clearance.
        deps: The RIPENER binding, call gate and store to write the result with.

    Returns:
        A CompactionResult holding the new SUMMARY entry and its token accounting.

    Raises:
        hivemind.memory.errors.EmptyCompactionError: `request.sources` is empty.
        hivemind.memory.errors.TooManySourcesError: `request.sources` has more entries than
            `hivemind.memory.bee_bread.entry.MAX_REF_IDS`.
        hivemind.memory.errors.SummaryOfSummaryError: A source's own `kind` is already `SUMMARY`.
        hivemind.memory.errors.ClearanceError: A source or pin is labelled above
            `request.clearance`.
    """
    _validate_request(request)
    counter = _select_counter(deps.bound)
    tokens_before = await _count_sources(request.sources, counter)

    result = await complete_structured(
        deps.bound, _build_request(deps.bound, request.sources), CompactionSchema, gate=deps.gate
    )

    clearance = _highest_clearance(request.sources, request.pins)
    text = _compose_entry_text(result.value, request.pins)
    tokens_after = await counter.count(text)
    entry = BeeBreadEntry(
        id=new_event_id(deps.ctx.clock),
        kind=BeeBreadEntryKind.SUMMARY,
        ref_ids=tuple(source.id for source in request.sources),
        task_id=request.task_id,
        clearance=clearance,
        created_at=deps.ctx.clock.now(),
        payload=text,
    )
    event = _compacted_event(entry, request.sources, tokens_before, tokens_after, deps.ctx)
    # Same store call as every other deposit in this package: the row and its event commit
    # together, or neither does (codingrules section 12).
    await deps.ctx.store.add_bee_bread_entry(entry, event)
    return CompactionResult(
        entry=entry,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        cost_usd=result.usage.cost_usd or 0.0,
    )


def _validate_request(request: CompactionRequest) -> None:
    """Refuse an empty, oversized, summary-of-summary or over-clearance compaction request."""
    if not request.sources:
        raise EmptyCompactionError()
    if len(request.sources) > MAX_REF_IDS:
        raise TooManySourcesError(len(request.sources), MAX_REF_IDS)
    for source in request.sources:
        # One level only (docs/adr/0022): a summary is never itself a source.
        if source.kind is BeeBreadEntryKind.SUMMARY:
            raise SummaryOfSummaryError(source.id)
        if source.clearance.rank > request.clearance.rank:
            raise ClearanceError(source.clearance, request.clearance)
    for pin in request.pins:
        # Pins reach the stored text verbatim (module docstring), so they are held to the same
        # ceiling as every source: the caller may never fold in a fact above its own clearance.
        if pin.clearance.rank > request.clearance.rank:
            raise ClearanceError(pin.clearance, request.clearance)


def _build_request(bound: BoundModel, sources: tuple[BeeBreadEntry, ...]) -> LLMRequest:
    """Build the one LLMRequest `compact` makes, assembled fresh from `sources` every call."""
    system = render(
        PromptName.COMPACT_RECORDS, sections={SectionLabel.RETRIEVED: _render_sources(sources)}
    )
    return LLMRequest(
        slot=bound.slot,
        system=system,
        messages=(Message.text(Role.USER, _ONE_LINE_INSTRUCTION),),
        max_output_tokens=RIPENER_OUTPUT_TOKENS,
    )


def _render_sources(sources: tuple[BeeBreadEntry, ...]) -> str:
    """Render every source entry as one labelled line, oldest first, for the RETRIEVED section."""
    lines = [
        f"[{source.id}] ({source.kind.value}) {source.text or source.payload or ''}"
        for source in sources
    ]
    return "\n".join(lines)


def _compose_entry_text(value: CompactionSchema, pins: tuple[Pin, ...]) -> str:
    """Render the structured result plus every pin's own text, verbatim, for BeeBreadEntry.payload.

    The model never sees `pins` (module docstring): they are appended here, after generation, so
    a pinned fact can never be paraphrased or dropped by the summarising call.
    """
    parts = [f"Summary:\n{value.summary}"]
    if value.key_facts:
        parts.append("Key facts:\n" + "\n".join(f"- {fact}" for fact in value.key_facts))
    if value.open_threads:
        parts.append("Open threads:\n" + "\n".join(f"- {thread}" for thread in value.open_threads))
    if pins:
        # Verbatim: pins.py.MAX_PIN_TEXT_CHARS already bounds pin.text, so no truncation here.
        parts.append("Pinned facts (verbatim):\n" + "\n".join(f"- {pin.text}" for pin in pins))
    return "\n\n".join(parts)


def _highest_clearance(sources: tuple[BeeBreadEntry, ...], pins: tuple[Pin, ...]) -> HoneyClearance:
    """Return the highest clearance among `sources` and `pins` (module docstring's deviation)."""
    ranked = [item.clearance for item in sources] + [pin.clearance for pin in pins]
    return max(ranked, key=lambda clearance: clearance.rank)


def _compacted_event(
    entry: BeeBreadEntry,
    sources: tuple[BeeBreadEntry, ...],
    tokens_before: int,
    tokens_after: int,
    ctx: MemoryContext,
) -> MemoryEvent:
    """Build the `memory.compacted` event `compact` records alongside the new entry.

    Counts and ids only, never text (codingrules section 12): the summary's own words live in
    `entry.payload`, never in a trail event's payload.
    """
    return MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind="memory.compacted",
        subject_id=entry.id,
        payload={
            "source_ids": [source.id for source in sources],
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        },
    )


async def _count_sources(sources: tuple[BeeBreadEntry, ...], counter: TokenCounter) -> int:
    """Return the summed token count of every source's own text or payload."""
    total = 0
    for source in sources:
        total += await counter.count(source.text or source.payload or "")
    return total


def _select_counter(bound: BoundModel) -> TokenCounter:
    """Return a provider-backed counter when RIPENER's own provider counts tokens, else an estimate.

    Mirrors `hivemind.workers.roles.drone.prompt.select_counter`'s own choice (codingrules section
    8.9: "provider count where available, else an estimate with margin").
    """
    estimate = EstimateCounter()
    if bound.provider.capabilities.token_counting:
        return ProviderCounter(bound.provider, bound.slot, estimate)
    return estimate
