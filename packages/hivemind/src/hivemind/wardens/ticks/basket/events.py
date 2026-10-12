"""Record the Basket events a Warden writes: `basket.*` on its trail, and the integrity guard row.

Every shipment, receipt, refusal and rejected deposit is a state change in the Hive's store of
blobs, so each writes a `PheromoneEvent` before it is considered done (codingrules section 12).
The events are the Basket's own (`hivemind.basket.events.basket_event`, subject the Cell's id);
this module only stamps them with the Warden's identity and records them. A blob that fails
verification on arrival also writes `guard.basket_integrity_failed`, the Guard's row, with the
Warden as its subject, because a corrupt or tampered shipment is a security fact and not only a
transfer outcome. Payloads carry hashes, sizes, codes and stage names -- never bytes, titles or
summaries (codingrules section 12).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Called by `receive`, `send` and `retain`. Calls into
    `hivemind.basket` (the event builder), `hivemind.cell` (CellIdentity) and
    `hivemind.pheromone` only.

Key invariants:
    - No payload holds blob bytes; `sha256` is a digest and `size_bytes` a count.
    - The subject is this Warden's Cell, or the Warden itself when it holds none yet.

See Also:
    - hivemind.basket.events for the builder and the facts a stored blob's events carry.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the event kinds.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.basket import TransferReceiver
from hivemind.basket.events import EventStamp, basket_event
from hivemind.cell.source import CellIdentity
from hivemind.pheromone import GuardEvent
from waggle.ids import CellId, new_event_id

if TYPE_CHECKING:
    from hivemind.wardens.deps import WardenDeps
    from hivemind.wardens.warden import Warden

__all__ = ["cell_identity", "own_cell_id", "record_basket", "record_integrity_failures"]


def cell_identity(deps: WardenDeps) -> CellIdentity:
    """Return the Hive, node and actor this Warden stamps its Cell-level records with."""
    return CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )


def own_cell_id(warden: Warden) -> CellId | None:
    """Return the id of the Cell this Warden serves, or None when it holds none.

    A Warden in WATCH has no Cell of its own; the Hive Stand's still knows which Cell its
    resident Basket belongs to, so that id stands in.
    """
    if warden._cell is not None:
        return warden._cell.id
    resident = warden._deps.resident_basket
    return resident.cell_id if resident is not None else None


async def record_basket(warden: Warden, kind: str, payload: Mapping[str, JsonValue]) -> None:
    """Record one `basket.*` event about this Warden's Cell.

    Args:
        warden: The recording Warden.
        kind: A `BasketEvent` kind (`basket.offered`, `basket.received`, ...).
        payload: Hashes, sizes and codes only.
    """
    deps = warden._deps
    cell_id = own_cell_id(warden)
    subject = str(cell_id) if cell_id is not None else str(warden._warden_id)
    stamp = EventStamp(cell_identity(deps), deps.clock)
    await deps.trail.record(basket_event(stamp, kind, subject, payload))


async def record_integrity_failures(warden: Warden, receiver: TransferReceiver) -> None:
    """Record `guard.basket_integrity_failed` for every failure `receiver` has noted.

    Args:
        warden: The recording Warden.
        receiver: A receiver that just refused a manifest or a whole blob; its failures are
            hashes and stages only.
    """
    deps = warden._deps
    for failure in receiver.integrity_failures:
        await deps.trail.record(
            GuardEvent(
                id=new_event_id(deps.clock),
                hive_id=deps.identity.hive_id,
                node_id=deps.identity.node_id,
                at=deps.clock.now(),
                actor=deps.identity.actor,
                kind="guard.basket_integrity_failed",
                subject_id=str(warden._warden_id),
                payload={
                    "sha256": failure.sha256,
                    "stage": failure.stage,
                    "received_bytes": failure.received_bytes,
                },
            )
        )
