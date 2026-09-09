"""Score and order a supervisor's inbox: the pure formula, the tie-break seam, and the Attendant.

The scoring formula, computed once per item by `score_item` and reused by `Attendant.score`::

    score = kind_weights[item.kind]                                   # base, by InboxKind
          + severity_weights[item.severity]     (if item.severity is set)
          + age_seconds * age_weight_per_s       (age_seconds = now - item.received_at)
          + task_link_weight                     (if item.task_id is set)
          + latency_weight / item.latency_budget_s   (if item.latency_budget_s is set)
    score *= principal_weights.get(item.principal, 1.0)                # one multiplier, last

Every additive term is optional and, when present, only ever adds (codingrules section 8.8:
"kind weight, severity weight, age x age_weight_per_s, task linkage bonus, latency budget urgency
[...], principal weight"); the principal weight is the one multiplicative step, applied once to
the whole sum, so a low-trust principal's items are scaled down uniformly rather than needing a
separate subtraction per factor. `score_item` is pure given `now` (codingrules section 11: no
`datetime.now()` inside it); `Attendant` is the effectful edge that reads a `Clock` for `order`
and, on an exact tie, awaits an optional `TieBreaker` (codingrules section 8.3: pure core,
effectful edges).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `Attendant` is built once per
    supervisor (`queen/inbox/`, `wardens/inbox/`) with that supervisor's own WeightTable and
    Clock. Calls into `hivemind.supervision.attendant.items`,
    `hivemind.supervision.attendant.weights` and waggle only.

Key invariants:
    - score_item never calls a clock itself; `now` is always the caller's.
    - Attendant.order is a stable sort by score descending; among exact ties it defers to
      `tie_breaker` if one is set, else falls back to (received_at ascending, id ascending) --
      older wins, and id breaks a same-instant tie deterministically.
    - tie_breaker.break_tie is awaited only for a group of two or more items whose scores compare
      equal; a group of one item is never a tie and never invokes it.

See Also:
    - .claude/codingrules.md section 8.8 for "every supervisor has an Attendant... deterministic
      first, a cheap slot for ties only where the grant allows".
    - .claude/codingrules.md section 11 for the injected-Clock rule score_item and Attendant follow.
    - hivemind.supervision.attendant.items for InboxItem, what score_item scores.
    - hivemind.supervision.attendant.weights for WeightTable and Priority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.supervision.attendant.items import InboxItem
from hivemind.supervision.attendant.weights import Priority, WeightTable
from waggle.clock import Clock

__all__ = ["Attendant", "TieBreaker", "score_item"]


class TieBreaker(Protocol):
    """Pick which of several exactly-tied InboxItems should rank first.

    The deterministic scorer above already orders every non-tied case; this seam exists only for
    the rare exact tie a model can arbitrate more sensibly than an arbitrary rule. The Queen may
    enable a model-backed implementation on `ModelSlot.ATTENDANT`; a Warden may enable one only
    within its Forage grant (codingrules section 8.8). The model-backed implementation itself
    lives in `queen/inbox/`, not here, so this package never imports `hivemind.llm`.
    """

    async def break_tie(self, items: tuple[InboxItem, ...]) -> InboxItem:
        """Return which of `items` (all scored equal) should rank first among them.

        Args:
            items: Two or more InboxItems whose Priority.score compares equal.

        Returns:
            The item from `items` that should lead; the rest keep their relative fallback order.
        """
        ...


def score_item(weights: WeightTable, item: InboxItem, now: datetime) -> Priority:
    """Score one InboxItem against `weights`, as of `now`.

    Args:
        weights: The supervisor's WeightTable.
        item: The item to score.
        now: The instant to measure `item`'s age against; always the caller's Clock, never read
            here (codingrules section 11).

    Returns:
        The item's Priority: its total score, and one reason string per factor that contributed,
        in the order they were added.
    """
    # Every additive term (kind, severity, age, task linkage, latency urgency) is computed by the
    # helper below; only the one multiplicative step (principal weight) happens here, so it is
    # visibly applied once, last, to the whole sum.
    score = 0.0
    reasons: list[str] = []
    for component, reason in _additive_terms(weights, item, now):
        score += component
        reasons.append(reason)

    # An unlisted principal is neutral (1.0), never penalised by omission.
    principal_weight = weights.principal_weights.get(item.principal, 1.0)
    score *= principal_weight
    reasons.append(f"principal={item.principal}:x{principal_weight:.3f}")

    return Priority(score=score, reasons=tuple(reasons))


def _additive_terms(
    weights: WeightTable, item: InboxItem, now: datetime
) -> list[tuple[float, str]]:
    """Return score_item's additive components, before the principal multiplier.

    Args:
        weights: The supervisor's WeightTable.
        item: The item to score.
        now: The instant to measure `item`'s age against.

    Returns:
        One `(component, reason)` pair per factor that applies to `item`: kind always; severity,
        task linkage and latency urgency only when `item` carries the field they read.
    """
    terms: list[tuple[float, str]] = []

    # Base weight by kind; a kind absent from the table (a caller's incomplete override) scores 0
    # for this factor rather than raising, so a partial WeightTable degrades gracefully.
    kind_component = weights.kind_weights.get(item.kind, 0.0)
    terms.append((kind_component, f"kind={item.kind.value}:{kind_component:+.3f}"))

    # Only an ALARM item carries a severity; every other kind's is None and contributes nothing.
    if item.severity is not None:
        severity_component = weights.severity_weights.get(item.severity, 0.0)
        terms.append(
            (severity_component, f"severity={item.severity.value}:{severity_component:+.3f}")
        )

    # Age always contributes, even at zero: a freshly received item's age is 0s, a visible reason
    # entry rather than a silently skipped factor.
    age_s = max((now - item.received_at).total_seconds(), 0.0)
    age_component = age_s * weights.age_weight_per_s
    terms.append((age_component, f"age={age_s:.1f}s:{age_component:+.3f}"))

    # A task-linked item (one that blocks or reports on a specific Task) gets a flat bonus: it is
    # more actionable than an item with nothing to act on.
    if item.task_id is not None:
        terms.append((weights.task_link_weight, f"task_linked:{weights.task_link_weight:+.3f}"))

    # Urgency is inversely proportional to the budget: a 1s budget scores latency_weight, a 100s
    # budget scores 1/100th of that. latency_budget_s is validated strictly positive at the
    # boundary (InboxItem), so this division never sees zero.
    if item.latency_budget_s is not None:
        urgency = weights.latency_weight / item.latency_budget_s
        terms.append((urgency, f"latency_budget={item.latency_budget_s:.1f}s:{urgency:+.3f}"))

    return terms


class Attendant:
    """Score and order one supervisor's inbox: deterministic first, a tie-breaker only on ties.

    Built once per supervisor with that supervisor's own WeightTable and Clock; `order` is the
    method a Queen or Warden tick calls once per pass over its inbox.
    """

    def __init__(
        self, weights: WeightTable, clock: Clock, tie_breaker: TieBreaker | None = None
    ) -> None:
        """Build an Attendant.

        Args:
            weights: This supervisor's WeightTable.
            clock: Source of `now` for `order`; `score` takes its own `now` explicitly instead, so
                it stays pure and testable without a clock at all.
            tie_breaker: An optional model-backed (or otherwise non-deterministic) arbiter for an
                exact score tie; None means every tie falls back to (received_at, id).
        """
        self._weights = weights
        self._clock = clock
        self._tie_breaker = tie_breaker

    def score(self, item: InboxItem, now: datetime) -> Priority:
        """Score `item` as of `now`; a thin, pure wrapper over `score_item`.

        Args:
            item: The item to score.
            now: The instant to measure `item`'s age against.

        Returns:
            `item`'s Priority.
        """
        return score_item(self._weights, item, now)

    async def order(self, items: tuple[InboxItem, ...]) -> tuple[InboxItem, ...]:
        """Return `items` ordered by score, highest priority first.

        Args:
            items: The inbox items to order.

        Returns:
            `items` sorted by score descending. An exact tie is broken by the configured
            `tie_breaker` if one is set (awaited once per tied group), else by received_at
            ascending (older wins) then id ascending.
        """
        now = self._clock.now()
        ranked = sorted(
            ((item, self.score(item, now)) for item in items),
            key=lambda pair: (-pair[1].score, pair[0].received_at, pair[0].id),
        )
        tie_breaker = self._tie_breaker
        if tie_breaker is None:
            return tuple(item for item, _ in ranked)
        return await self._settle_ties(ranked, tie_breaker)

    async def _settle_ties(
        self, ranked: list[tuple[InboxItem, Priority]], tie_breaker: TieBreaker
    ) -> tuple[InboxItem, ...]:
        """Move each tied group's tie_breaker winner to the front of its group.

        Args:
            ranked: Items paired with their Priority, already sorted by the fallback order.
            tie_breaker: The arbiter to consult, once per group of two or more equal scores.

        Returns:
            `ranked`'s items, with each tied group's winner (per `tie_breaker`) leading that
            group; a group of exactly one item is left untouched.
        """
        result = [item for item, _ in ranked]
        start = 0
        while start < len(ranked):
            end = start + 1
            # Scores are already sorted, so equal scores are contiguous: extend the group while
            # the next item's score matches the group's first.
            while end < len(ranked) and ranked[end][1].score == ranked[start][1].score:
                end += 1
            if end - start > 1:
                group = tuple(item for item, _ in ranked[start:end])
                winner = await tie_breaker.break_tie(group)
                rest = [item for item in group if item is not winner]
                result[start:end] = [winner, *rest]
            start = end
        return tuple(result)
