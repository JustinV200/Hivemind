"""Define the shapes compaction reads and writes: the request, its deps, the result, the schema.

Split from `hivemind.memory.compact.run` (which holds the one effectful function, `compact`) purely
by codingrules 5.2 ("one concept per file"): this module is the plain data every call passes or
receives, `run` is the one function that moves between them. `CompactionSchema` is what
`hivemind.llm.complete_structured` asks `ModelSlot.RIPENER` to fill in -- a short summary, a bounded
list of key facts, a bounded list of open threads (roadmap step 4.3's own field list). `
CompactionRequest` is what to compact: the source `BeeBreadEntry` rows, the pins to copy verbatim,
and the calling principal's own clearance ceiling. `CompactionDeps` is the RIPENER binding, call
gate and store `compact` needs beyond its request. `CompactionResult` is one call's outcome: the
stored entry and its token accounting.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.compact`. Read
    and built by `hivemind.memory.compact.run.compact`, its one caller. Calls into hivemind.cell
    (HoneyClearance), hivemind.llm (BoundModel, CallGate), hivemind.memory.bee_bread.entry
    (BeeBreadEntry), hivemind.memory.context (MemoryContext), hivemind.memory.pins (Pin) and waggle
    only.

Key invariants:
    - Every model here is frozen (codingrules section 8.5): `CompactionSchema` because it crosses
      the LLM boundary, `CompactionRequest`/`CompactionDeps`/`CompactionResult` because they are
      internal value bundles, like every other collaborator bundle in this package
      (`hivemind.memory.context.MemoryContext`).

See Also:
    - .claude/roadmap.md step 4.3 for CompactionSchema's field list verbatim.
    - hivemind.memory.compact.run for compact, the one function these shapes pass through.
    - hivemind.memory.context for MemoryContext, the pattern CompactionDeps mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.llm import BoundModel, CallGate
from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.context import MemoryContext
from hivemind.memory.pins import Pin
from waggle.ids import TaskId

# A paragraph or two, matching Handoff.progress's own scale (hivemind.memory.handoff): a compacted
# summary is meant to be read at a glance, not re-read in full like the records it replaces.
MAX_SUMMARY_CHARS = 4_000
MAX_KEY_FACTS = 20  # A bounded bullet list, not a second transcript.
MAX_KEY_FACT_CHARS = 500
MAX_OPEN_THREADS = 20
MAX_OPEN_THREAD_CHARS = 500

__all__ = [
    "MAX_KEY_FACTS",
    "MAX_KEY_FACT_CHARS",
    "MAX_OPEN_THREADS",
    "MAX_OPEN_THREAD_CHARS",
    "MAX_SUMMARY_CHARS",
    "CompactionDeps",
    "CompactionRequest",
    "CompactionResult",
    "CompactionSchema",
]


class CompactionSchema(BaseModel):
    """The structured shape `complete_structured` asks `ModelSlot.RIPENER` to fill in.

    Never rendered back to a model as an example (codingrules section 8.6: a prompt never
    hard-codes its own JSON shape); the ladder itself supplies this schema to whichever rung is
    active.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=MAX_SUMMARY_CHARS,
        description="A short prose account of what the source records together represent.",
    )
    key_facts: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_KEY_FACTS,
        description="Facts worth remembering on their own, each capped and one per line.",
    )
    open_threads: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_OPEN_THREADS,
        description="Questions or follow-ups the source records leave unresolved.",
    )


@dataclass(frozen=True, slots=True)
class CompactionRequest:
    """What to compact: the source records, the pins to copy verbatim, and the caller's clearance.

    Attributes:
        sources: The Bee Bread entries to summarise, already looked up (by task or time window,
            through `hivemind.memory.bee_bread.BeeBread`) by the caller. Never empty; never more
            than `hivemind.memory.bee_bread.entry.MAX_REF_IDS` (`compact` refuses both).
        pins: Pins to copy verbatim into the resulting entry's text; never shown to the model
            (`hivemind.memory.compact.run`'s own module docstring, "Key invariants").
        clearance: The compacting principal's own clearance ceiling; `compact` refuses a source or
            pin labelled above it (`hivemind.memory.errors.ClearanceError`).
        task_id: The task every source concerns, when there is one shared task; `None` when the
            batch spans no single task (mirrors `hivemind.memory.bee_bread.BeeBreadEntry.task_id`).
    """

    sources: tuple[BeeBreadEntry, ...]
    pins: tuple[Pin, ...]
    clearance: HoneyClearance
    task_id: TaskId | None


@dataclass(frozen=True, slots=True)
class CompactionDeps:
    """The collaborators `compact` needs beyond its request: the RIPENER binding and the store.

    Attributes:
        bound: The `ModelSlot.RIPENER` binding `complete_structured` calls (docs/adr/0022).
        gate: The seam that call passes through (`hivemind.llm.ladders.gate.CallGate`).
        ctx: Where the resulting entry and its `memory.compacted` event are written.
    """

    bound: BoundModel
    gate: CallGate
    ctx: MemoryContext


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """One `compact` call's outcome: the stored entry and its token accounting.

    Attributes:
        entry: The new `BeeBreadEntryKind.SUMMARY` entry, already durably stored.
        tokens_before: The summed token count of every source's own text/payload, before
            compaction (the `memory.compacted` event's own `tokens_before`).
        tokens_after: `entry`'s own token count, pins included (`tokens_after` on the same event).
        cost_usd: What the one structured call cost, or `0.0` when the binding reports no cost.
    """

    entry: BeeBreadEntry
    tokens_before: int
    tokens_after: int
    cost_usd: float
