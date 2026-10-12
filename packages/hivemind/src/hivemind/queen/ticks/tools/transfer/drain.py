"""Ship the blobs an operator queued with `hive basket ship`, once the destination Warden is here.

`hive basket ship` only queues a row (`BasketStore.queue_shipment`): the Queen's process is the one
that holds the Wardens' links. Every few seconds her housekeeping asks the store for the shipments
still pending, finds the attached Warden whose Cell is the destination and ships the blob to it as a
background job. A shipment completes when the Warden verified the blob, or when the blob or the
Warden refused it by rule (that is on the trail as `basket.refused`; asking again every few seconds
would change nothing). One that failed for want of a link, or timed out, stays pending and is
tried again.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools.
    transfer`. Called by `hivemind.queen.ticks.housekeeping.run_housekeeping` on every tick; calls
    into `hivemind.basket` (the store's queue), `.ship` and `hivemind.queen.ticks.tools.jobs`.

Key invariants:
    - The store is read at most once per `DRAIN_INTERVAL_S`, and never while the lane is closed.
    - Each queued shipment has at most one job (`ToolLane.draining`), so a slow transfer is not
      offered twice.
    - Nothing is awaited on a Warden's reply inside the tick; the job does that.

See Also:
    - hivemind.cli.basket for `hive basket ship`, the writer of the queue.
"""

from __future__ import annotations

from collections.abc import Iterable

from hivemind.basket.store.rows import Shipment
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.tools.jobs import start_background
from hivemind.queen.ticks.tools.transfer.ship import ShipResult, ship

DRAIN_INTERVAL_S = 5.0  # How often the queue is read: an operator's request, not a latency path.

log = get_logger(__name__)

__all__ = ["DRAIN_INTERVAL_S", "drain_shipments"]


async def drain_shipments(deps: QueenDeps, wardens: Iterable[WardenLink]) -> None:
    """Start a job for every queued shipment whose destination Cell has an attached Warden.

    Args:
        deps: The Queen's collaborators; `royal_jelly.basket` holds the queue.
        wardens: Every Warden currently attached.
    """
    access, lane = deps.royal_jelly, deps.tool_lane
    now = deps.clock.now()
    if access is None or lane.closed:
        return
    if (
        lane.last_drain_at is not None
        and (now - lane.last_drain_at).total_seconds() < DRAIN_INTERVAL_S
    ):
        return
    lane.last_drain_at = now
    # Local SQLite read; the queue is a handful of rows an operator typed.
    pending = await access.basket.pending_shipments()
    by_cell = {link.cell.id: link for link in wardens}
    for shipment in pending:
        link = by_cell.get(shipment.destination_cell_id)
        if link is None or shipment.id in lane.draining:
            continue  # Its Warden is not here yet, or a job already carries it.
        lane.draining.add(shipment.id)
        start_background(deps, _ship_queued(deps, link, shipment))


async def _ship_queued(deps: QueenDeps, link: WardenLink, shipment: Shipment) -> None:
    """Ship one queued blob and mark the row done unless the failure may pass."""
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - `drain_shipments` returned early without one.
    try:
        result = await ship(deps, link, shipment.sha256, shipment.reason)
        if result is not ShipResult.FAILED:
            # Shipped, or refused by rule: either way the same request would end the same way.
            await access.basket.complete_shipment(shipment.id)
    finally:
        deps.tool_lane.draining.discard(shipment.id)
