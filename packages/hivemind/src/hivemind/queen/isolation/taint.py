"""Taint an isolated Cell's memory from the first event that justified isolating it.

ADR-0035, "Taint is one label": isolation (roadmap step 10.6a) is one of its three setters, and
this module is the isolation's one call to `hivemind.memory.taint.taint_memory`. What it covers is
the Cell's memory from the first trail event the isolation cites (the Guard report's oldest
evidence) onward; with no evidence (the human isolating on their own judgement) it covers what
was written from the moment the isolation began, the checkpoints its pause just asked for. The
memory tables carry no Cell id, so the Cell is named the way `TaintScope.for_cell` expects: by
the bees that ran on it (its Warden, and every sub-bee its Warden spawned for a task placed
there) and by the tasks placed there (every `queen.assigned` naming the Cell). Tainted memory
stays tainted until a judge clears it (`hivemind.memory.taint.clear_taint`), a lift included:
the resume of a paused task waits on that verdict (`hivemind.queen.quarantine.release`).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by `hivemind.queen.isolation.path`. Calls into
    `hivemind.memory` (MemoryContext, the taint setter), `hivemind.pheromone` (TrailQuery), the
    sub-package's own order and waggle only; `QueenDeps` only for its type.

Key invariants:
    - The only `taint_memory` call on the isolation path, always with `TaintSource.ISOLATION` and
      the `cell.isolated` event as its cause (tests/unit/memory/taint/test_only_setter.py).
    - Every item it labels has its own `memory.tainted` row; the count it returns is those rows.

See Also:
    - hivemind.memory.taint.scope.TaintScope.for_cell for the isolation shape.
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.memory import MemoryContext
from hivemind.memory.taint import TaintScope, TaintSource, TaintStamp, taint_memory
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from hivemind.queen.isolation.order import IsolationOrder
from hivemind.queen.isolation.pause import SPAWNED_KIND
from waggle.ids import EventId, TaskId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

ASSIGNED_KIND = "queen.assigned"  # Subject: the task; payload names the Cell it was placed on.

__all__ = ["ASSIGNED_KIND", "taint_cell"]


async def taint_cell(
    deps: QueenDeps,
    link: WardenLink,
    order: IsolationOrder,
    cause_event_id: EventId,
    began_at: datetime,
) -> int:
    """Label the isolated Cell's memory TAINTED from the order's first evidence on.

    Args:
        deps: The Queen's collaborators; `memory` holds the labels, `trail` names the bees.
        link: The isolated Cell's Warden.
        order: The isolation; its evidence, oldest first, says from when.
        cause_event_id: The `cell.isolated` event every `memory.tainted` row names as its cause.
        began_at: When the isolation began: the start when the order cites no evidence.

    Returns:
        How many items were newly labelled.
    """
    tasks = await _cell_tasks(deps, link)
    bees = {link.warden_id, *await _cell_bees(deps, tasks)}
    if order.evidence:
        scope = TaintScope.for_cell(bees, tasks, order.evidence[0])
    else:
        # The human's own judgement cites nothing: taint what the paused bees wrote since.
        scope = TaintScope(authors=frozenset(bees), task_ids=frozenset(tasks), since=began_at)
    stamp = TaintStamp(
        source=TaintSource.ISOLATION,
        reason=f"Cell {order.cell_id} isolated ({order.ordered_by.value})"
        + (f" on Guard report {order.report_id}" if order.report_id else ""),
        cause_event_id=cause_event_id,
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    report = await taint_memory(scope, stamp, ctx)
    return len(report.tainted)


async def _cell_tasks(deps: QueenDeps, link: WardenLink) -> frozenset[TaskId]:
    """Return every task the Queen ever assigned to `link`'s Cell (the scope bounds the time)."""
    query = TrailQuery(kind=ASSIGNED_KIND, newest_first=True, limit=MAX_QUERY_LIMIT)
    events = await deps.trail.query(query)
    return frozenset(
        TaskId(event.subject_id)
        for event in events
        if str(event.payload.get("cell_id")) == link.cell.id
    )


async def _cell_bees(deps: QueenDeps, tasks: frozenset[TaskId]) -> frozenset[str]:
    """Return every sub-bee a Warden spawned for one of `tasks`."""
    if not tasks:
        return frozenset()
    query = TrailQuery(kind=SPAWNED_KIND, newest_first=True, limit=MAX_QUERY_LIMIT)
    events = await deps.trail.query(query)
    return frozenset(
        event.subject_id for event in events if str(event.payload.get("task_id")) in tasks
    )
