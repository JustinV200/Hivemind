"""Provide the Warden's Basket tick handlers: receive a shipment, ship up, deposit what is retained.

The Basket (ADR-0048) is the Hive's store of inert blobs, and a Warden touches it three ways.
`receive` takes a blob the Queen ships down to the Warden's Cell (refusing by rule before a byte
moves, verifying hash and signature before keeping one); `send` ships a blob up to her (offer,
chunks cut from her acknowledgements, a future the caller awaits); `retain` is the `RetainSink` a
sub-bee's `WorkerContext` carries, which re-checks a staged file and deposits it with provenance.
`handle` routes a `basket.*` payload to `receive` or `send`; `door` builds a Warden's way to
deposit and describe a blob to Honey; `source` serves a Worker's Comb tools their package bytes;
`events` records the `basket.*` and `guard.basket_integrity_failed` rows; `state` and `jobs` are
the transfers in flight and the background work a Warden owns. Each function takes the owning
`Warden` and reads or writes its private state directly, like every tick handler.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks sub-package. Called by `hivemind.wardens.ticks.dispatch.act` for `WardenAction.BASKET`,
    by `hivemind.wardens.spawn` through the sink factory, and by `hivemind.wardens.ticks.tools`.

Key invariants:
    - A blob is inert: nothing here executes, installs or loads one.
    - Nothing here awaits a model, and no tick awaits the Queen: waits are futures that later
      ticks resolve, inside background jobs the Warden owns.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decisions.
    - hivemind.basket for the state machines and stores these handlers drive.

Public API (roadmap step 9.2a):
    - handle_basket_item (handle): the `BASKET` action's one entry point.
    - ship_up, request_timeout_s (send): ship a blob up and wait; the shipment's patience.
    - WardenRetainSink, is_tainted (retain): deposit what a Worker retains.
    - deposit_door, node_keys, send_nectar, DeferredHoneyPort (door): depositing and Nectar.
    - package_source, blob_verified, store_of (source): serving and checking package bytes.
    - BasketTransfers (state), start_job (jobs): the Warden's transfers and its owned jobs.
"""

from hivemind.wardens.ticks.basket.door import (
    DeferredHoneyPort,
    deposit_door,
    node_keys,
    send_nectar,
)
from hivemind.wardens.ticks.basket.handle import handle_basket_item
from hivemind.wardens.ticks.basket.jobs import start_job
from hivemind.wardens.ticks.basket.retain import WardenRetainSink, is_tainted
from hivemind.wardens.ticks.basket.send import request_timeout_s, ship_up
from hivemind.wardens.ticks.basket.source import blob_verified, package_source, store_of
from hivemind.wardens.ticks.basket.state import BasketTransfers

__all__ = [
    "BasketTransfers",
    "DeferredHoneyPort",
    "WardenRetainSink",
    "blob_verified",
    "deposit_door",
    "handle_basket_item",
    "is_tainted",
    "node_keys",
    "package_source",
    "request_timeout_s",
    "send_nectar",
    "ship_up",
    "start_job",
    "store_of",
]
