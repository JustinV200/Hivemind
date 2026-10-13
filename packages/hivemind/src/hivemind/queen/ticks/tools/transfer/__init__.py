"""Move Basket blobs between the Queen and her Wardens: ship one out, receive one in, drain a queue.

The Queen is the Basket's only canonical store (ADR-0048), and every transfer goes through her,
because nodes connect out to the Queen. A blob goes out with `ship_blob` (the sandbox runner, the
announcer and the queued-shipment drain all use it) and its acknowledgements are handled by
`handle_basket_reply`; a blob a Warden ships up is handled by `handle_basket_inbound`, which
verifies it and re-deposits it through the Basket's one door. `ship_blob` is the job's half and
awaits; the two handlers are the tick's half and only advance a state machine.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Used by
    the tools dispatch, the sandbox runner, the announcer and `queen.ticks.housekeeping`.

Key invariants:
    - The tick never waits on a Warden's reply; a job does, on a future the tick resolves.
    - The Queen's store keeps every blob; a Warden's resident copy is its own concern.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md.

Public API:
    - ship_blob, ship, ShipResult (ship): send one blob and await the outcome.
    - handle_basket_reply (ship): a Warden's ack or refusal drives the shipment.
    - handle_basket_inbound, spool_dir (receive): a Warden's upload in.
    - drain_shipments (drain): the queue `hive basket ship` fills.
"""

from hivemind.queen.ticks.tools.transfer.drain import DRAIN_INTERVAL_S, drain_shipments
from hivemind.queen.ticks.tools.transfer.receive import handle_basket_inbound, spool_dir
from hivemind.queen.ticks.tools.transfer.ship import (
    ShipResult,
    handle_basket_reply,
    ship,
    ship_blob,
)

__all__ = [
    "DRAIN_INTERVAL_S",
    "ShipResult",
    "drain_shipments",
    "handle_basket_inbound",
    "handle_basket_reply",
    "ship",
    "ship_blob",
    "spool_dir",
]
