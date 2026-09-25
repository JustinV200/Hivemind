"""Taint this Cell's own memory on the Queen's order: the isolation setter inside a Virtual Cell.

ADR-0043, "Taint is one label": isolation (roadmap step 10.6a) is one of its three setters. The
Queen runs it on the Hive's own tables when she isolates a Cell (`hivemind.queen.isolation.
taint`), but a Virtual Cell's Warden keeps its memory store inside the Cell (ADR-0027), where her
label cannot reach: the Handoffs its bees checkpointed, their episode records, its own. So she
sends a `CellTaintOrder`, and this module is its Warden carrying it out: the same setter
(`hivemind.memory.taint.taint_memory`), with `TaintSource.ISOLATION`, her `cell.isolated` event as
every label's cause, and her scope (the bees and tasks she names, from the instant she names),
widened by what only this Warden sees: itself and the bees it runs now. It then ships its trail
segment at once, so the `memory.tainted` rows reach her trail before the next heartbeat would carry
them. Idempotent: an item already labelled is left as it is, so her resend on a reattached link
changes nothing. The labels are what `hivemind.wardens.isolation.gate` refuses a resume by.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    isolation sub-package. Called by `hivemind.wardens.warden.Warden`'s tick for a Queen-sent
    `CellTaintOrder`. Calls into `hivemind.common.logging`, `hivemind.memory` (MemoryContext),
    `hivemind.memory.taint` (the one setter), `hivemind.wardens.ticks.trail_ship` and waggle
    only.

Key invariants:
    - This is the only `taint_memory` call in `wardens/` besides the quarantine path, always with
      `TaintSource.ISOLATION` and the Queen's `cell.isolated` event as its cause
      (tests/unit/memory/taint/test_only_setter.py allow-lists exactly this module).
    - An order naming another Cell labels nothing: a Warden taints its own store only.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.queen.isolation.taint for the Queen's half, and the order she sends.
    - docs/guard/isolation.md, "The isolation path".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.memory import MemoryContext
from hivemind.memory.taint import TaintScope, TaintSource, TaintStamp, taint_memory
from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from waggle.messages.cell import CellTaintOrder

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

log = get_logger(__name__)

__all__ = ["taint_own_memory"]


async def taint_own_memory(warden: Warden, order: CellTaintOrder) -> int | None:
    """Label this Cell's own memory TAINTED as the Queen's `order` says.

    Args:
        warden: The Warden of the isolated Cell; its store, identity and trail are written.
        order: The Queen's order: the Cell, the bees and tasks, the instant and the cause.

    Returns:
        How many items were newly labelled; None when the order names another Cell.
    """
    cell = warden._cell
    if cell is None or order.cell_id != cell.id:
        # Misrouted, or this Warden holds no Cell: it never labels memory that is not its own.
        log.warning(
            "warden.taint_order_ignored", warden_id=warden._warden_id, cell_id=order.cell_id
        )
        return None
    deps = warden._deps
    stamp = TaintStamp(
        source=TaintSource.ISOLATION, reason=order.reason, cause_event_id=order.cause_event_id
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    report = await taint_memory(_scope(warden, order), stamp, ctx, deps.taint_ledgers)
    # The Queen reads the labels off her own trail: this Cell's rows go now, not on the next beat.
    await ship_trail_before_result(warden)
    log.info(
        "warden.isolation_tainted",
        warden_id=warden._warden_id,
        cell_id=order.cell_id,
        cause_event_id=order.cause_event_id,
        tainted_count=len(report.tainted),
    )
    return len(report.tainted)


def _scope(warden: Warden, order: CellTaintOrder) -> TaintScope:
    """The order's bees and tasks, plus this Warden and every bee it runs now, from its instant."""
    authors = {*order.authors, warden._warden_id, *warden._sub_bees}
    task_ids = {*order.task_ids, *(sub_bee.task_id for sub_bee in warden._sub_bees.values())}
    return TaintScope(
        authors=frozenset(authors), task_ids=frozenset(task_ids), since=order.suspect_at
    )
