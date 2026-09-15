"""Score a hot-state candidate's relevance: pure, deterministic, no I/O (roadmap step 4.1).

Hot state (codingrules section 8.9) must pack the highest-value items into a bounded budget, not
just the newest ones. `score` is the one function that decides value: it combines an exponential
recency decay (an item's usefulness fades the longer it has sat unread), a flat bonus when the item
names a task that is still active ("task linkage"), a bonus that grows with an Alarm's severity, and
a large, non-decaying floor for anything pinned (codingrules section 8.9: "pins that never decay").
`Scorable` is the tagged union every hot-state candidate belongs to -- the same flat summary models
`hivemind.memory.hot_state.summaries` already defines (`hivemind.memory` may not import
`hivemind.brood_chamber` or `hivemind.supervision`, so there is no `Task`/`Alarm` object to score
here, only their flat summaries), plus `Note` and `Pin` (the two memory-owned hot-state rows).
`item_id` and `item_timestamp` are this module's own per-type dispatch, exported so
`hivemind.memory.hot_state.packing` and `hivemind.memory.demote` share one place that knows how to
read an id or a recency key off any Scorable, instead of each re-deriving it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by
    `hivemind.memory.hot_state.packing.assemble` (to rank candidates before packing) and
    `hivemind.memory.demote.should_demote` (to read an item's own timestamp). Calls into
    hivemind.memory.hot_state.summaries, hivemind.memory.notes, hivemind.memory.pins and waggle
    only.

Key invariants:
    - `score` is pure: given the same item, `now`, `active_tasks` and `pins`, it always returns
      the same RelevanceScore. It performs no I/O and reads no clock of its own.
    - Holding everything else fixed, `score(...).value` is monotone non-increasing as `now` moves
      later (recency only ever decays, never grows).
    - A pinned item's score is always `PIN_FLOOR` or higher, which is always greater than the
      maximum reachable score of an unpinned item (`1.0 + TASK_LINKAGE_BONUS + max(_SEVERITY_
      BONUS.values())`), so a pin always sorts before every non-pin regardless of age, linkage or
      severity.

See Also:
    - .claude/codingrules.md section 8.9 for the recency-decay, linkage, severity and pin rules
      this module implements.
    - .claude/roadmap.md step 4.1 for score's signature and the property tests it names.
    - .claude/roadmap.md step 4.2a for Cell Wax, the linkage extension this module leaves room for
      but does not implement.
    - hivemind.memory.hot_state.packing for assemble, the main caller.
    - hivemind.memory.demote for should_demote, the other caller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from hivemind.memory.hot_state.summaries import (
    AlarmSummary,
    DecisionSummary,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from waggle.ids import TaskId

# Half-life of an item's recency contribution, in seconds. Six hours: long enough that a decision
# made earlier in the same working session still reads as fresh, short enough that yesterday's
# chatter is clearly less relevant than this hour's without being scored at zero (recency is only
# one of several terms; it should nudge ordering, not dominate it after a day).
RECENCY_HALF_LIFE_S = 6.0 * 3600.0

# Flat bonus added when an item names a task in `active_tasks`. A flat value (not a multiplier)
# keeps the bonus's size independent of how large the recency term happens to be, so "this concerns
# work still in flight" always moves an item up by the same amount regardless of its age.
TASK_LINKAGE_BONUS = 2.0

# A pinned item's floor. The maximum an unpinned item can ever reach is 1.0 (recency, which decays
# from at most 1.0) + TASK_LINKAGE_BONUS (2.0) + the highest severity bonus (2.5) = 5.5; 100.0 is
# comfortably above that with a wide margin, so a pin always outranks every non-pin regardless of
# how the other terms combine, and still leaves room for a small recency-driven tie-break among
# several pins (codingrules section 8.9: "pins that never decay").
PIN_FLOOR = 100.0

# Bonus by AlarmSummary.severity's own wire string (mirrors hivemind.supervision.alarm.
# AlarmSeverity's three members by value; hivemind.memory may not import hivemind.supervision, so
# AlarmSummary.severity is already carried as a plain string -- see that model's own docstring).
# Strictly increasing with severity, so a CRITICAL Alarm never scores below a WARNING one, which
# never scores below an INFO one, all else equal.
_SEVERITY_BONUS: dict[str, float] = {"INFO": 0.0, "WARNING": 1.0, "CRITICAL": 2.5}

__all__ = [
    "PIN_FLOOR",
    "RECENCY_HALF_LIFE_S",
    "TASK_LINKAGE_BONUS",
    "RelevanceScore",
    "Scorable",
    "item_id",
    "item_timestamp",
    "score",
]

# Every hot-state candidate `score` and its callers know how to read: the flat summaries
# hivemind.memory.hot_state.summaries defines, plus the two rows hivemind.memory itself writes.
Scorable = TaskSummary | AlarmSummary | QuestionSummary | DecisionSummary | Note | Pin


@dataclass(frozen=True, slots=True)
class RelevanceScore:
    """One candidate's relevance: higher packs first, lower is dropped first on overflow.

    Attributes:
        value: The score itself. Only ever compared to other RelevanceScores from the same
            `score` call site; the absolute number carries no meaning on its own.
    """

    value: float


def score(
    item: Scorable, now: datetime, active_tasks: frozenset[TaskId], pins: frozenset[str]
) -> RelevanceScore:
    """Score `item`'s relevance from recency decay, task linkage, Alarm severity and pin status.

    Args:
        item: The candidate to score.
        now: The reference time recency decays against; injected so this stays pure and testable
            with a FakeClock-derived timestamp rather than the wall clock.
        active_tasks: Ids of tasks still open. An item that names one of these (directly, as a
            TaskSummary, or via a `task_id` field) gets TASK_LINKAGE_BONUS added.
        pins: Ids of items currently pinned. `item` counts as pinned when it is itself a `Pin` or
            when `item_id(item)` is a member of this set; a pinned item's score is floored at
            PIN_FLOOR (see that constant for why it always then outranks every non-pin).

    Returns:
        The item's RelevanceScore; higher is more relevant.
    """
    # Recency: exponential decay from 1.0 at age zero, so it never grows and never goes negative
    # even if `now` is (implausibly) earlier than the item's own timestamp -- max() guards that.
    age_s = max((now - item_timestamp(item)).total_seconds(), 0.0)
    recency = math.exp(-age_s / RECENCY_HALF_LIFE_S)

    total = recency + _linkage_bonus(item, active_tasks) + _severity_bonus(item)
    if _is_pinned(item, pins):
        # Extension point (roadmap step 4.2a, Cell Wax): a later step adds a `cells_in_play`-style
        # linkage term here, scored the same additive way, so a caution about a Cell only raises
        # relevance while that Cell is a placement/assignment candidate. Not implemented in 4.1/4.2.
        return RelevanceScore(PIN_FLOOR + total)
    return RelevanceScore(total)


def item_id(item: Scorable) -> str:
    """Return the id `score`'s callers track `item` by; DecisionSummary's is named differently.

    Args:
        item: The candidate to read an id from.

    Returns:
        `item.episode_id` for a DecisionSummary, `item.id` for every other Scorable variant.
    """
    match item:
        case DecisionSummary():
            return item.episode_id
        case TaskSummary() | AlarmSummary() | QuestionSummary() | Note() | Pin():
            return item.id
        case _ as unreachable:
            assert_never(unreachable)


def item_timestamp(item: Scorable) -> datetime:
    """Return the timestamp `score` decays `item`'s recency term against.

    Args:
        item: The candidate to read a timestamp from.

    Returns:
        The field each Scorable variant calls its own recency key: `updated_at` for a TaskSummary,
        `raised_at` for an AlarmSummary, `asked_at` for a QuestionSummary, `at` for a
        DecisionSummary, `written_at` for a Note, `created_at` for a Pin.
    """
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
        case Pin():
            return item.created_at
        case _ as unreachable:
            assert_never(unreachable)


def _is_pinned(item: Scorable, pins: frozenset[str]) -> bool:
    """Return whether `item` counts as pinned: a Pin itself, or named in `pins` by id."""
    return isinstance(item, Pin) or item_id(item) in pins


def _linkage_bonus(item: Scorable, active_tasks: frozenset[TaskId]) -> float:
    """Return TASK_LINKAGE_BONUS when `item` names a task in `active_tasks`, else 0.0.

    Never negative: a task closing (leaving `active_tasks`) simply withholds the bonus rather than
    penalising the item, matching roadmap step 4.1's "task linkage never lowers a score".
    """
    match item:
        case TaskSummary():
            linked = item.id in active_tasks
        case AlarmSummary():
            linked = item.task_id is not None and item.task_id in active_tasks
        case QuestionSummary():
            linked = item.task_id in active_tasks
        case DecisionSummary() | Note() | Pin():
            # None of these carry a task_id field: no linkage to score either way.
            linked = False
        case _ as unreachable:
            assert_never(unreachable)
    return TASK_LINKAGE_BONUS if linked else 0.0


def _severity_bonus(item: Scorable) -> float:
    """Return the Alarm-severity bonus: only AlarmSummary carries a severity to score."""
    if isinstance(item, AlarmSummary):
        return _SEVERITY_BONUS.get(item.severity, 0.0)
    return 0.0
