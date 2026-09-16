"""Pure rules for what leaves hot state, plus the one function that moves it (roadmap step 4.2).

Demotion is a duty, not an emergency (codingrules section 8.9): "A House Bee sweep moves items that
no longer link to an active task from hot state to Bee Bread." `should_demote` is the pure decision
-- given a candidate, the current time, which tasks are still active, which Alarms have resolved
and how long an item may sit unlinked before it ages out, it returns a typed `DemotionReason` or
`None`. `demote` is the one effectful step: it archives the item into Bee Bread (the warm memory
tier, `hivemind.memory.bee_bread.deposit.deposit_hot_state_item`) and, for a Note (the one hot-state
row `hivemind.memory` itself owns the storage of), removes it from that storage too. A House Bee
sweep (roadmap step 4.3, another dispatch) is what calls these on a timer; this module only exposes
the pure rules and the one write path it needs.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `should_demote` and `demote` will be
    called by workers.roles.house_bee's sweep duty (roadmap step 4.3, not implemented by this
    dispatch). Calls into hivemind.memory.bee_bread (deposit_hot_state_item), hivemind.memory.
    context (MemoryContext), hivemind.memory.hot_state.summaries, hivemind.memory.notes,
    hivemind.memory.pins, hivemind.memory.relevance (Scorable, item_timestamp) and waggle only.

Key invariants:
    - `should_demote` is pure: no I/O, and it never mutates `item`. It is total over every
      Scorable variant (a `match` with `assert_never` on the wildcard).
    - `should_demote` never returns a reason for a Pin: pins never decay (codingrules section 8.9).
    - `demote`'s caller must never pass a Pin (see hivemind.memory.bee_bread.deposit.
      deposit_hot_state_item, which raises InvariantViolationError if one reaches it anyway).

See Also:
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency".
    - .claude/roadmap.md step 4.2 for should_demote's signature verbatim.
    - hivemind.memory.relevance for Scorable and item_timestamp, this module's other half.
    - hivemind.memory.bee_bread for deposit_hot_state_item, demote's one write path.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import assert_never

from hivemind.memory.bee_bread import BeeBreadEntry, deposit_hot_state_item
from hivemind.memory.context import MemoryContext
from hivemind.memory.hot_state.summaries import (
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.relevance import Scorable, item_timestamp
from waggle.ids import AlarmId, TaskId

__all__ = ["DemotionReason", "demote", "should_demote"]


class DemotionReason(Enum):
    """Why an item is leaving hot state."""

    TASK_CLOSED = "TASK_CLOSED"  # The task it names is no longer in the active set.
    ALARM_RESOLVED = "ALARM_RESOLVED"  # The Alarm it is has been resolved.
    AGED_OUT = "AGED_OUT"  # It has sat unlinked and unresolved past the manifest's hot window.


def should_demote(
    item: Scorable,
    now: datetime,
    active_tasks: frozenset[TaskId],
    resolved_alarms: frozenset[AlarmId],
    window: timedelta,
) -> DemotionReason | None:
    """Decide whether `item` should leave hot state, and why.

    Args:
        item: The candidate to check.
        now: The reference time `window` is measured against.
        active_tasks: Ids of tasks still open. An item naming one not in this set is TASK_CLOSED.
        resolved_alarms: Ids of Alarms already resolved. An AlarmSummary naming one is
            ALARM_RESOLVED.
        window: How long an item may sit in hot state, unlinked and unresolved, before it counts
            as AGED_OUT (the manifest's `[memory] hot_window_s`, as a timedelta).

    Returns:
        The first reason that applies, checked in the order TASK_CLOSED, ALARM_RESOLVED, AGED_OUT;
        `None` when none applies and `item` should stay in hot state. Always `None` for a Pin or a
        CellWaxSummary.
    """
    if isinstance(item, Pin):
        # Pins never decay (codingrules section 8.9); a House Bee sweep must never call demote()
        # for one, but should_demote itself stays total and safe regardless of what asks.
        return None
    if isinstance(item, CellWaxSummary):
        # Cell Wax has its own lifecycle (hivemind.memory.cell_wax.writes, driven by a Queen
        # decision or the House Bee sweep's own wax-expiry hook), never the generic hot-state
        # demotion path a Note or a summary walks; see hivemind.memory.bee_bread.deposit's own
        # matching refusal for why demote() must never be called with one.
        return None

    linked_task = _linked_task(item)
    if linked_task is not None and linked_task not in active_tasks:
        return DemotionReason.TASK_CLOSED

    if isinstance(item, AlarmSummary) and item.id in resolved_alarms:
        return DemotionReason.ALARM_RESOLVED

    if now - item_timestamp(item) > window:
        return DemotionReason.AGED_OUT

    return None


async def demote(item: Scorable, ctx: MemoryContext) -> BeeBreadEntry:
    """Move `item` out of hot state: archive it into Bee Bread and remove any store row it owns.

    Args:
        item: The candidate to demote; never a Pin (see the module docstring).
        ctx: The store, identity and clock to write with.

    Returns:
        The BeeBreadEntry `item` was archived as.
    """
    entry = await deposit_hot_state_item(item, ctx)
    if isinstance(item, Note):
        # Notes are the one hot-state row hivemind.memory itself stores (memory_notes); every
        # other Scorable variant is only ever a view onto Brood Chamber or the trail, which memory
        # does not own and so has nothing of its own to remove here.
        await ctx.store.remove_note(item.id)
    return entry


def _linked_task(item: Scorable) -> TaskId | None:
    """Return the task id `item` names, or None when it names none."""
    match item:
        case TaskSummary():
            return item.id
        case AlarmSummary():
            return item.task_id
        case QuestionSummary():
            return item.task_id
        case DecisionSummary() | CellWaxSummary() | Note() | Pin():
            return None
        case _ as unreachable:
            assert_never(unreachable)
