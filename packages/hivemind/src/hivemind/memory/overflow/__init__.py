"""Recover from ContextTooLong: shrink the budget and retry, up to MAX_OVERFLOWS (roadmap 4.4).

See `hivemind.memory.overflow.retry` for the full explanation.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Calls into `hivemind.llm.errors`,
    `hivemind.memory.context`, `hivemind.memory.errors`, `hivemind.memory.hot_state.summaries`,
    `hivemind.pheromone` and waggle only.

Key invariants:
    - None beyond `hivemind.memory.overflow.retry`'s own; this module only re-exports.

See Also:
    - hivemind.memory.overflow.retry for shrink, run_with_overflow_retry and ContextOverflowError.

Public API:
    - MAX_OVERFLOWS, SHRINK_FACTOR, MIN_BUDGET_TOKENS: the retry loop's own constants.
    - ContextOverflowError: raised after MAX_OVERFLOWS retries.
    - shrink: the pure budget-shrinking function.
    - run_with_overflow_retry: the retry loop itself.
"""

from hivemind.memory.overflow.retry import (
    MAX_OVERFLOWS,
    MIN_BUDGET_TOKENS,
    SHRINK_FACTOR,
    ContextOverflowError,
    run_with_overflow_retry,
    shrink,
)

__all__ = [
    "MAX_OVERFLOWS",
    "MIN_BUDGET_TOKENS",
    "SHRINK_FACTOR",
    "ContextOverflowError",
    "run_with_overflow_retry",
    "shrink",
]
