"""Define shrink, run_with_overflow_retry and ContextOverflowError: recover from ContextTooLong.

Roadmap step 4.4: "`ContextTooLong` from any provider shrinks the budget for that episode and
retries, recording `memory.overflow`; three overflows raise an Alarm." An awake episode (a Queen's,
a Warden's or a Drone's one stateless pass over hot state -- codingrules section 8.8) assembles a
prompt against a `hivemind.memory.hot_state.summaries.TokenBudget` and then calls a model; the
internal budget is only ever an estimate (`hivemind.memory.counter.EstimateCounter`'s own margin,
or a provider's own count) of what the bound model will actually accept, so the provider itself can
still reject the call with `hivemind.llm.errors.ContextTooLongError` (ADR-0022: "a `ContextTooLong`
error shrinks the budget and retries; it never crashes a bee"). `run_with_overflow_retry` is that
retry loop, generic over any `episode` callable that takes a budget and returns a result: it is the
one place this shrink-record-retry shape lives, so the Queen's, a Warden's and a Drone's own
episode functions each supply their own "assemble a prompt at this budget and call the model"
closure and share this module's own loop, budget-shrinking and `memory.overflow` trail event.
`shrink` is the pure half: a fixed multiplicative factor per overflow, floored so a budget can
never shrink to something too small to hold even the triggering event's own text.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.overflow`.
    Called by `hivemind.queen.awake.episode.decide_awake`, `hivemind.wardens.awake.episode.
    decide_awake` and `hivemind.workers.roles.drone.role.Drone.run` -- the three places a prompt is
    assembled and a model called (roadmap step 4.4). Calls into `hivemind.llm.errors`
    (ContextTooLongError), `hivemind.memory.context` (MemoryIdentity), `hivemind.memory.errors`
    (MemoryTierError), `hivemind.memory.hot_state.summaries` (TokenBudget), `hivemind.pheromone`
    (MemoryEvent, PheromoneTrail) and waggle only.

Key invariants:
    - `shrink` is pure: same `(budget, attempt)` always yields the same smaller `TokenBudget`,
      with `max_input_tokens` never dropping below `MIN_BUDGET_TOKENS`.
    - `run_with_overflow_retry` records exactly one `memory.overflow` event per
      `ContextTooLongError` caught, before deciding whether to retry or raise -- so the trail shows
      every attempt, not only the ones that led to a retry.
    - After `MAX_OVERFLOWS` overflows in one call, `run_with_overflow_retry` raises
      `ContextOverflowError` instead of retrying a further time; it never loops unboundedly
      (ADR-0022: "three overflows... raise an Alarm rather than looping").

See Also:
    - .claude/roadmap.md step 4.4 for this module's own requirement, verbatim.
    - docs/adr/0022-memory-tiers-relevance-and-compaction.md for "overflow shrinks and retries; it
      never crashes".
    - hivemind.llm.errors for ContextTooLongError, the signal this module reacts to.
    - hivemind.memory.hot_state.summaries for TokenBudget, the value this module shrinks.
    - hivemind.pheromone for MemoryEvent, the `memory.overflow` event this module records.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar

from hivemind.llm.errors import ContextTooLongError
from hivemind.memory.context import MemoryIdentity
from hivemind.memory.errors import MemoryTierError
from hivemind.memory.hot_state.summaries import TokenBudget
from hivemind.pheromone import MemoryEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import new_event_id

MAX_OVERFLOWS = 3  # ADR-0022: three overflows in one episode raise an Alarm, rather than looping.
# Each overflow shrinks max_input_tokens to SHRINK_FACTOR of what it just was: aggressive enough
# that a retry is likely to fit (packing drops the lowest-scored items first, ADR-0022, so a
# smaller budget mostly sheds the least useful content), gentle enough that three shrinks in a row
# still leave real room (0.6**3 ~= 22% of the original) before MIN_BUDGET_TOKENS ever binds.
SHRINK_FACTOR = 0.6
MIN_BUDGET_TOKENS = 256  # A budget below this could not hold even a short triggering event's text.

__all__ = [
    "MAX_OVERFLOWS",
    "MIN_BUDGET_TOKENS",
    "SHRINK_FACTOR",
    "ContextOverflowError",
    "run_with_overflow_retry",
    "shrink",
]


class ContextOverflowError(MemoryTierError):
    """Raise when one episode has overflowed its context MAX_OVERFLOWS times.

    The typed signal `run_with_overflow_retry`'s caller turns into an `Alarm` of kind
    `AlarmKind.CONTEXT_OVERFLOW` (roadmap step 4.4): a Drone lets this propagate through its own
    existing crash-to-Alarm path (`hivemind.workers.runtime.attempt.AttemptManager`); the Queen and
    a Warden catch it and record it through theirs.
    """

    code: ClassVar[str] = "hivemind.memory.context_overflow"

    def __init__(self, attempts: int) -> None:
        """Build the error for an episode that overflowed too many times.

        Args:
            attempts: How many `ContextTooLongError`s this episode caught before giving up;
                always `MAX_OVERFLOWS` when raised by `run_with_overflow_retry`.
        """
        super().__init__(
            f"Context overflowed {attempts} time(s) in one episode, at the MAX_OVERFLOWS="
            f"{MAX_OVERFLOWS} limit; the episode is abandoned rather than retried further."
        )
        self.attempts = attempts


def shrink(budget: TokenBudget, attempt: int) -> TokenBudget:
    """Return `budget` shrunk for retry number `attempt`, floored at MIN_BUDGET_TOKENS.

    Pure (codingrules section 8.3): no I/O, and the same inputs always yield the same output.

    Args:
        budget: The budget the provider just rejected as too long.
        attempt: Which overflow this is within the episode, starting at 1 for the first.

    Returns:
        A new `TokenBudget` with `max_input_tokens` scaled by `SHRINK_FACTOR ** attempt` (floored
        at `MIN_BUDGET_TOKENS`); every other field is unchanged.
    """
    factor = SHRINK_FACTOR**attempt
    shrunk_tokens = max(MIN_BUDGET_TOKENS, int(budget.max_input_tokens * factor))
    return budget.model_copy(update={"max_input_tokens": shrunk_tokens})


async def run_with_overflow_retry[T](
    episode: Callable[[TokenBudget], Awaitable[T]],
    budget: TokenBudget,
    trail: PheromoneTrail,
    identity: MemoryIdentity,
    clock: Clock,
) -> T:
    """Run `episode(budget)`, shrinking and retrying on ContextTooLongError, up to MAX_OVERFLOWS.

    Args:
        episode: Assembles a prompt at the given budget and calls the bound model; the one thing
            this function retries. Raises `hivemind.llm.errors.ContextTooLongError` when the
            provider rejects the call as too long for its window.
        budget: The episode's starting budget.
        trail: Where each overflow's `memory.overflow` event is recorded.
        identity: The Hive, node and actor stamped on every `memory.overflow` event.
        clock: Injected time source for every id minted and every timestamp written.

    Returns:
        Whatever `episode` returned, from whichever attempt first succeeded.

    Raises:
        ContextOverflowError: `episode` raised `ContextTooLongError` `MAX_OVERFLOWS` times in a
            row; the caller turns this into an `Alarm` of kind `AlarmKind.CONTEXT_OVERFLOW`.
    """
    recorder = _Recorder(trail=trail, identity=identity, clock=clock)
    current = budget
    overflow_count = 0
    while True:
        try:
            # External await: one episode attempt, which itself makes one model call (latency
            # class seconds); a ContextTooLongError here means the provider rejected the call
            # outright, never a timeout, so no separate timeout wraps this await.
            return await episode(current)
        except ContextTooLongError as exc:
            overflow_count += 1
            new_budget = shrink(current, overflow_count)
            await _record_overflow(recorder, overflow_count, current, new_budget)
            if overflow_count >= MAX_OVERFLOWS:
                # ADR-0022: three overflows raise an Alarm rather than looping a further time.
                raise ContextOverflowError(overflow_count) from exc
            current = new_budget


@dataclass(frozen=True, slots=True)
class _Recorder:
    """The trail, identity and clock `_record_overflow` writes a `memory.overflow` event with."""

    trail: PheromoneTrail
    identity: MemoryIdentity
    clock: Clock


async def _record_overflow(
    recorder: _Recorder, attempt: int, old_budget: TokenBudget, new_budget: TokenBudget
) -> None:
    """Record one `memory.overflow` event: the attempt number and the old and new budgets."""
    identity = recorder.identity
    event_id = new_event_id(recorder.clock)
    event = MemoryEvent(
        id=event_id,
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=recorder.clock.now(),
        actor=identity.actor,
        kind="memory.overflow",
        # No task or bee id is guaranteed for every caller (a Queen-level episode may concern
        # none); the event's own id is always available, mirroring hivemind.memory.checkpoint's
        # own fallback for a Handoff with no task.
        subject_id=event_id,
        payload={
            "attempt": attempt,
            "old_max_input_tokens": old_budget.max_input_tokens,
            "new_max_input_tokens": new_budget.max_input_tokens,
        },
    )
    await recorder.trail.record(event)
