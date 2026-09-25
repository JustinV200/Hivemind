"""Taint an isolated Cell's memory from the first event that justified isolating it, both halves.

ADR-0043, "Taint is one label": isolation (roadmap step 10.6a) is one of its three setters, and
this module is the isolation's one call to `hivemind.memory.taint.taint_memory` on the Hive's own
tables. What it covers is the Cell's memory from the instant the isolation suspects (the Guard
report's oldest evidence, or, with none, the moment the isolation began, so the checkpoints its
pause just asked for are covered) onward. The memory tables carry no Cell id, so the Cell is named
the way `TaintScope` expects: by the bees that ran on it (its Warden, and every sub-bee its Warden
spawned for a task placed there) and by the tasks placed there (every `queen.assigned` naming it),
newest first up to the order's bounds, since a task placed long before the suspect instant has
finished writing. A Virtual Cell's Warden keeps its own memory store inside the Cell (ADR-0027),
where this label cannot reach, so the same scope and cause then travel to it as a `CellTaintOrder`,
and it runs the same setter over its own store (`hivemind.wardens.isolation`). A closed link loses
that order, so `resend_taint_order` sends it again whenever the Cell's Warden attaches while the
isolation stands; the Warden's setter is idempotent. Tainted memory stays tainted until a judge
clears it (`hivemind.memory.taint.clear_taint`), a lift included. A Night Veil Cell's bees, tasks
and records live in its segment while it lives, which every read here reaches (`query_cell`);
its rows in the Hive's own tables are not labelled, because each label's `memory.tainted` row
reaches the durable trail with it and would outlive the Cell (codingrules 12), and every one of
those rows goes at its teardown anyway (`MemoryStore.purge_night_veil`). Its Warden still labels
the store inside the Cell, whose records stay in the Cell's segment.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by `hivemind.queen.isolation.path` and, for the resend,
    `hivemind.queen.attach`. Calls into `hivemind.common.logging`, `hivemind.hive.night_veil`
    (is_night_veil_cell), `hivemind.memory` (MemoryContext, the taint setter),
    `hivemind.pheromone` (TrailQuery, query_cell), the sub-package's own order, pause and record
    modules, and waggle only; `QueenDeps` only for its type.

Key invariants:
    - The only `taint_memory` call on the Queen's isolation path, always with
      `TaintSource.ISOLATION` and the `cell.isolated` event as its cause
      (tests/unit/memory/taint/test_only_setter.py).
    - The order a Warden gets names the very scope and cause the Hive's tables were labelled
      with; a lost order is sent again on the Warden's next attach while the isolation stands.
    - Nothing here records a `memory.tainted` row about a Night Veil Cell on the durable trail.

See Also:
    - hivemind.wardens.isolation.taint for the Warden's half.
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.hive.night_veil import is_night_veil_cell
from hivemind.memory import MemoryContext
from hivemind.memory.taint import TaintScope, TaintSource, TaintStamp, taint_memory
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery, query_cell
from hivemind.queen.isolation.pause import SPAWNED_KIND
from hivemind.queen.isolation.record import IsolationState, read_isolation
from waggle.envelope import wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import CellId, EventId, TaskId
from waggle.messages.cell import CellTaintOrder
from waggle.messages.cell.taint import MAX_TAINT_AUTHORS, MAX_TAINT_TASKS

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

ASSIGNED_KIND = "queen.assigned"  # Subject: the task; payload names the Cell it was placed on.

log = get_logger(__name__)

__all__ = ["ASSIGNED_KIND", "resend_taint_order", "taint_cell", "taint_reason"]


async def taint_cell(
    deps: QueenDeps, link: WardenLink, reason: str, cause_event_id: EventId, suspect_at: datetime
) -> int:
    """Label the isolated Cell's memory TAINTED from `suspect_at` on, and order its Warden to.

    Args:
        deps: The Queen's collaborators; `memory` holds the labels, `trail` names the bees.
        link: The isolated Cell's Warden, which the order travels over.
        reason: Why, naming ids only (`taint_reason`).
        cause_event_id: The `cell.isolated` event every `memory.tainted` row names as its cause.
        suspect_at: From when memory is suspect: the first evidence, or the isolation's start.

    Returns:
        How many items of the Hive's own tables were newly labelled: none for a Night Veil Cell,
        whose rows there are not labelled (module docstring).
    """
    order = await _order(deps, link, reason, cause_event_id, suspect_at)
    tainted = 0
    # The Hive's tables first, then the store only the Cell's Warden can reach.
    if not await is_night_veil_cell(deps.trail, link.cell.id):
        tainted = await _taint_tables(deps, order)
    await _send(deps, link, order)
    return tainted


async def resend_taint_order(deps: QueenDeps, link: WardenLink) -> bool:
    """Send an attaching Warden its Cell's taint order again, while the isolation stands.

    Args:
        deps: The Queen's collaborators; `trail` holds the standing `cell.isolated`.
        link: The Warden attaching now.

    Returns:
        True when an isolation stands on its Cell and the order was sent again.
    """
    record = await read_isolation(deps, link.cell.id)
    event = record.isolated
    if record.state is not IsolationState.ISOLATED or event is None:
        return False  # The common case: nothing stands, and nothing is sent.
    payload = event.payload
    suspect = payload.get("suspect_at")
    suspect_at = datetime.fromisoformat(suspect) if isinstance(suspect, str) else event.at
    reason = taint_reason(link.cell.id, payload.get("ordered_by"), payload.get("report_id"))
    order = await _order(deps, link, reason, event.id, suspect_at)
    return await _send(deps, link, order)


def taint_reason(cell_id: CellId, ordered_by: JsonValue, report_id: JsonValue) -> str:
    """Return every isolation label's reason: the Cell, who isolated it, and the report, if any.

    Args:
        cell_id: The isolated Cell.
        ordered_by: The `Isolator` value who ordered it ("queen" or "human").
        report_id: The Guard report the isolation answers, or None.

    Returns:
        A sentence naming ids only, never content.
    """
    answering = f" on Guard report {report_id}" if report_id else ""
    return f"Cell {cell_id} isolated ({ordered_by}){answering}"


async def _order(
    deps: QueenDeps, link: WardenLink, reason: str, cause_event_id: EventId, suspect_at: datetime
) -> CellTaintOrder:
    """Build the scope, as the order a Warden gets: the Cell's bees and tasks, the instant, why."""
    tasks = await _cell_tasks(deps, link)
    # The Warden itself, then its sub-bees newest first, within the order's bound.
    bees = (link.warden_id, *(await _cell_bees(deps, link, tasks))[: MAX_TAINT_AUTHORS - 1])
    return CellTaintOrder.model_validate(
        {
            "cell_id": link.cell.id,
            "cause_event_id": cause_event_id,
            "suspect_at": suspect_at,
            "authors": bees,
            "task_ids": tasks,
            "reason": reason,
        }
    )


async def _taint_tables(deps: QueenDeps, order: CellTaintOrder) -> int:
    """Label the Hive's own tables with `order`'s scope and cause; return how many items."""
    scope = TaintScope(
        authors=frozenset(order.authors), task_ids=frozenset(order.task_ids), since=order.suspect_at
    )
    stamp = TaintStamp(
        source=TaintSource.ISOLATION, reason=order.reason, cause_event_id=order.cause_event_id
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    return len((await taint_memory(scope, stamp, ctx)).tainted)


async def _send(deps: QueenDeps, link: WardenLink, order: CellTaintOrder) -> bool:
    """Send `order` to the Cell's Warden; a closed link is not a reason to stop isolating."""
    try:
        await link.transport.send(wrap(order, link.hop, clock=deps.clock))
    except (TransportClosedError, ConnectionLostError):
        # Sent again when the Warden next attaches while the isolation stands (module docstring).
        log.warning("queen.taint_order_undelivered", cell_id=order.cell_id)
        return False
    return True


async def _cell_tasks(deps: QueenDeps, link: WardenLink) -> tuple[TaskId, ...]:
    """Return the tasks the Queen assigned to `link`'s Cell, newest first, within the bound."""
    query = TrailQuery(kind=ASSIGNED_KIND, newest_first=True, limit=MAX_QUERY_LIMIT)
    # A Night Veil Cell's placements are in its segment alone: `query_cell` reads it too.
    events = await query_cell(deps.trail, link.cell.id, query)
    placed = (TaskId(e.subject_id) for e in events if str(e.payload.get("cell_id")) == link.cell.id)
    # dict.fromkeys keeps the first (newest) sighting of each task, in order.
    return tuple(dict.fromkeys(placed))[:MAX_TAINT_TASKS]


async def _cell_bees(
    deps: QueenDeps, link: WardenLink, tasks: tuple[TaskId, ...]
) -> tuple[str, ...]:
    """Return the sub-bees `link`'s Warden spawned for one of `tasks`, newest first."""
    if not tasks:
        return ()
    query = TrailQuery(kind=SPAWNED_KIND, newest_first=True, limit=MAX_QUERY_LIMIT)
    events = await query_cell(deps.trail, link.cell.id, query)
    wanted = frozenset(tasks)
    spawned = (e.subject_id for e in events if str(e.payload.get("task_id")) in wanted)
    return tuple(dict.fromkeys(spawned))
