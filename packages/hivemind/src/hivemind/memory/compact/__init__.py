"""Compaction: fold Bee Bread source records into one summary on ModelSlot.RIPENER (step 4.3).

Docs/adr/0022 fixes the shape: summarise only from source records, never from a previous summary
(so drift is bounded to one hop), and copy every pin verbatim. `schema` holds the plain data one
`compact` call passes or receives (`CompactionSchema`, the structured shape the model fills in;
`CompactionRequest`, `CompactionDeps`, `CompactionResult`); `run` holds the one effectful function,
`compact`, that ties them together: assemble the prompt fresh from the sources, call
`hivemind.llm.complete_structured` on the RIPENER binding, copy the pins in verbatim, deposit the
result as a new `SUMMARY` `BeeBreadEntry`, and record `memory.compacted` in the same store call.
This face only re-exports (codingrules section 5.4); the split from a single flat module into this
package is purely a codingrules 5.6 fan-out fix (`hivemind.memory` was already at ten flat modules)
with no responsibility split of its own -- see `hivemind.memory.compact.run`'s own docstring for
the module that actually explains the design.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory`. Called by
    `hivemind.workers.roles.house_bee.sweep.run_sweep`'s compaction phase (roadmap step 4.3, a
    sibling dispatch). Calls into `hivemind.cell`, `hivemind.llm`, `hivemind.memory.bee_bread`,
    `hivemind.memory.context`, `hivemind.memory.counter`, `hivemind.memory.errors`,
    `hivemind.memory.pins`, `hivemind.pheromone` and waggle only.

Key invariants:
    - `compact` never summarises a source whose own `kind` is `BeeBreadEntryKind.SUMMARY`; the
      resulting entry's own `kind` is always `SUMMARY` (one level, by construction).

See Also:
    - docs/adr/0022-memory-tiers-relevance-and-compaction.md for the decision this package makes
      real.
    - .claude/roadmap.md step 4.3 for this package's spec verbatim.
    - hivemind.memory.compact.run for compact itself, and the full design rationale.
    - hivemind.memory.demote for should_demote/demote, the step that fills Bee Bread this package
      later compacts.

Public API:
    - CompactionSchema, CompactionRequest, CompactionDeps, CompactionResult: the plain data one
      `compact` call passes or receives (schema).
    - MAX_SUMMARY_CHARS, MAX_KEY_FACTS, MAX_KEY_FACT_CHARS, MAX_OPEN_THREADS,
      MAX_OPEN_THREAD_CHARS: CompactionSchema's own field caps (schema).
    - compact, RIPENER_OUTPUT_TOKENS: the one effectful function, and its output budget (run).
"""

from hivemind.memory.compact.run import RIPENER_OUTPUT_TOKENS, compact
from hivemind.memory.compact.schema import (
    MAX_KEY_FACT_CHARS,
    MAX_KEY_FACTS,
    MAX_OPEN_THREAD_CHARS,
    MAX_OPEN_THREADS,
    MAX_SUMMARY_CHARS,
    CompactionDeps,
    CompactionRequest,
    CompactionResult,
    CompactionSchema,
)

__all__ = [
    "MAX_KEY_FACTS",
    "MAX_KEY_FACT_CHARS",
    "MAX_OPEN_THREADS",
    "MAX_OPEN_THREAD_CHARS",
    "MAX_SUMMARY_CHARS",
    "RIPENER_OUTPUT_TOKENS",
    "CompactionDeps",
    "CompactionRequest",
    "CompactionResult",
    "CompactionSchema",
    "compact",
]
