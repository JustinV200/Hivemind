"""Tests for hivemind.memory.overflow: shrink and run_with_overflow_retry.

Fits into the Hive:
    Mirrors src/hivemind/memory/overflow.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.overflow for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.llm.errors import ContextTooLongError
from hivemind.memory.context import MemoryIdentity
from hivemind.memory.hot_state.summaries import TokenBudget
from hivemind.memory.overflow import (
    MAX_OVERFLOWS,
    MIN_BUDGET_TOKENS,
    ContextOverflowError,
    run_with_overflow_retry,
    shrink,
)
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


def _budget(max_input_tokens: int = 10_000) -> TokenBudget:
    return TokenBudget(max_input_tokens=max_input_tokens, output_reserve=1_000)


def _identity(clock: FakeClock) -> MemoryIdentity:
    return MemoryIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")


def test_shrink_is_pure_and_shrinks_by_the_documented_factor() -> None:
    budget = _budget(10_000)

    once = shrink(budget, 1)
    twice = shrink(budget, 2)

    assert once.max_input_tokens < budget.max_input_tokens
    assert twice.max_input_tokens < once.max_input_tokens
    assert once.output_reserve == budget.output_reserve
    # Purity: calling again with the same inputs gives the same answer.
    assert shrink(budget, 1) == once


def test_shrink_never_drops_below_the_minimum_floor() -> None:
    budget = _budget(300)

    shrunk = shrink(budget, 3)

    assert shrunk.max_input_tokens >= MIN_BUDGET_TOKENS


async def test_run_with_overflow_retry_returns_on_first_success_with_no_shrink() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = _identity(clock)
    budget = _budget()

    async def episode(given: TokenBudget) -> TokenBudget:
        return given

    result = await run_with_overflow_retry(episode, budget, trail, identity, clock)

    assert result == budget
    events = await trail.query(TrailQuery())
    assert events == ()  # No overflow at all: nothing recorded.


async def test_run_with_overflow_retry_shrinks_and_retries_then_succeeds() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = _identity(clock)
    budget = _budget()
    seen_budgets: list[TokenBudget] = []

    async def episode(given: TokenBudget) -> str:
        seen_budgets.append(given)
        if len(seen_budgets) < 2:
            raise ContextTooLongError("fake", window=1_000, requested=2_000)
        return "done"

    result = await run_with_overflow_retry(episode, budget, trail, identity, clock)

    assert result == "done"
    assert len(seen_budgets) == 2
    # The second attempt's budget is strictly smaller than the first's.
    assert seen_budgets[1].max_input_tokens < seen_budgets[0].max_input_tokens
    events = await trail.query(TrailQuery())
    assert [e.kind for e in events] == ["memory.overflow"]
    assert events[0].payload["attempt"] == 1
    assert events[0].payload["old_max_input_tokens"] == budget.max_input_tokens
    assert events[0].payload["new_max_input_tokens"] == seen_budgets[1].max_input_tokens


async def test_run_with_overflow_retry_raises_after_max_overflows() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = _identity(clock)
    budget = _budget()
    attempts = 0

    async def episode(given: TokenBudget) -> str:
        nonlocal attempts
        attempts += 1
        raise ContextTooLongError("fake", window=1_000, requested=2_000)

    with pytest.raises(ContextOverflowError) as exc_info:
        await run_with_overflow_retry(episode, budget, trail, identity, clock)

    assert attempts == MAX_OVERFLOWS
    assert exc_info.value.attempts == MAX_OVERFLOWS
    events = await trail.query(TrailQuery())
    assert [e.kind for e in events] == ["memory.overflow"] * MAX_OVERFLOWS
