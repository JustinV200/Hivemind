"""Define evict_source: the Queen takes back seats she borrowed from a Warden's model server.

A Cell's Warden (its always-on supervisor) loads models on its own Cell autonomously, and may lend
some of the seats (concurrent requests a model server allows) to the shared pool, which the Queen
(the central orchestrator) can then hand to other Cells (``Ceilings.exportable_seats``, codingrules
section 8.10). When she needs those seats back she may order the Warden to stop offering the model,
and *only* for that reason: "the Queen may later evict a local model only to reclaim seats she has
exported". ``evict_source`` enforces the rule on her side by reading her ledger: the order is sent
only when the Warden's last local-pool report shows it exported seats, the source is hosted on that
Warden's own Cell, and the map knows the source. Anything else sends nothing and returns False, so
a caller cannot evict a model whose seats were never lent. The Warden checks the same rule again
on its side (``hivemind.wardens.ticks.control.handle_source_evict``), and answers a real eviction
with ``forage.source_removed`` (cause ``EVICTED``), which the Queen's source handler applies.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by whatever policy decides seats must be reclaimed (a later step; nothing
    in the kernel's tick calls it yet). Calls into ``hivemind.queen.deps`` (QueenDeps, WardenLink),
    ``hivemind.queen.trail`` (record_forage_event) and waggle only.

Key invariants:
    - Nothing is sent unless the ledger shows ``seats_exported > 0`` for that Warden: the Queen
      never evicts a model whose seats were not lent.
    - ``seats_to_reclaim`` never exceeds what the Warden exported.
    - Exactly one ``forage.source_evicted`` trail row is recorded per order sent, and none for a
      refusal.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - hivemind.queen.ticks.sources for the handler of the ``forage.source_removed`` that answers.
    - waggle.messages.forage.sources for SourceEvict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.forage import UnknownSourceError
from hivemind.queen.trail import record_forage_event
from waggle.envelope import wrap
from waggle.messages.forage import SourceEvict

if TYPE_CHECKING:
    # Only for the type hints below: hivemind.queen.deps imports hivemind.queen.forage.ledger, so
    # a real import here would cycle (the same note as hivemind.queen.forage.ceilings).
    from hivemind.queen.deps import QueenDeps, WardenLink

log = get_logger(__name__)

__all__ = ["evict_source"]


async def evict_source(deps: QueenDeps, warden: WardenLink, source_id: str, seats: int) -> bool:
    """Order ``warden`` to stop offering ``source_id``, if the Queen exported its seats.

    Args:
        deps: The Queen's collaborators: her map, ledger, clock and trail.
        warden: The Warden whose model server hosts the source, and its link.
        source_id: The Forage map source to evict.
        seats: How many seats the Queen needs back; at least 1.

    Returns:
        True when a ``forage.source_evict`` was sent; False when the ledger shows no exported
        seats for this Warden, the map does not know the source, it is hosted on another Cell, or
        the link had already closed.
    """
    report = deps.ledger.local_report_for(warden.warden_id)
    # The rule itself: no exported seats, nothing to reclaim, so nothing may be evicted.
    if report is None or report.seats_exported <= 0 or seats <= 0:
        log.info("queen.evict.refused", warden_id=warden.warden_id, why="no exported seats")
        return False
    try:
        source = deps.map.get(source_id)
    except UnknownSourceError:
        log.info("queen.evict.refused", warden_id=warden.warden_id, why="unknown source")
        return False
    # Only a source on this Warden's own Cell is the Warden's to give back.
    if source.spec.host_cell_id != warden.cell.id:
        log.info("queen.evict.refused", warden_id=warden.warden_id, why="not hosted there")
        return False
    reclaim = min(seats, report.seats_exported)
    order = SourceEvict(
        cell_id=warden.cell.id,
        source=source.source_ref(),
        seats_to_reclaim=reclaim,
        reason="The Queen reclaims seats she exported from this Cell's model server.",
    )
    # Sent before it is recorded, like every Queen order: the trail never shows an order that was
    # not at least handed to the link.
    if not await warden.send(wrap(order, warden.hop, clock=deps.clock)):
        return False
    await record_forage_event(
        deps,
        "forage.source_evicted",
        warden.cell.id,
        warden_id=warden.warden_id,
        source_id=source_id,
        seats_to_reclaim=reclaim,
    )
    return True
