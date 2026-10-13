"""Run the Royal Jelly Lab for the Queen: tool requests, sandboxes, promotions and Basket traffic.

A Worker that needs a capability it lacks asks its Warden, which asks the Queen. The Queen is the
only party that scaffolds and promotes a hive-scope tool (ADR-0046), proves it in a disposable
sandbox Cell (ADR-0047), and moves the package to every Cell that can run it as a Basket blob
(ADR-0048). This package is her side of all that, and every long step is a job beside her tick,
never an await inside it (codingrules 8.8): `requests` validates a `tool.request` and starts the
promotion flow, `runner` is the flow's sandbox (provision, ship, order, await the report,
release), `quarantine` brokers a Warden's own sandbox request and routes a sandbox Warden's report,
`announce` ships and announces a promotion, `promotions` writes a cell-scope tool a remote Warden
promoted, `review` is her awake judgement of a tool only half provable in the sandbox, `honey`
describes a promotion to the Honey Store, and `transfer` moves blobs both ways.
`handle_tool_item` is the one door `queen.ticks.liveness` opens for all of it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.ticks.liveness.handle_infrastructure_item` for every
    inbox item, by `hivemind.queen.attach` (a newly attached Warden's catch-up),
    `hivemind.queen.ticks.housekeeping` (queued shipments) and `hivemind.queen.queen` (stop).

Key invariants:
    - The tick starts a job, resolves a future or advances a state machine; it never waits for a
      model, a sandbox or a Warden's reply.
    - `queen.autopilot` imports nothing here: the review runs in a job, never in `decide`.
    - Every reply to a Warden is a `control.error` or a message correlated to the request.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md, docs/adr/0047-quarantine-comb-gates-and-
      sandbox.md and docs/adr/0048-basket-inert-blobs-residency-and-transfer.md.
    - hivemind.royal_jelly.promotion for the flow these modules supply the effects of.

Public API:
    - handle_tool_item (this module): the dispatch for every tool and Basket message.
    - announce_current_tools, QueenAnnouncer (announce): a Warden's catch-up and a promotion's news.
    - drain_shipments (transfer): the queued `hive basket ship` requests.
    - stop_tools, forget_warden (jobs): cancel every job as the Queen stops; end what waits on a
      Warden that detached.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.tools.announce import QueenAnnouncer, announce_current_tools
from hivemind.queen.ticks.tools.jobs import forget_warden, stop_tools
from hivemind.queen.ticks.tools.promotions import handle_remote_promotion
from hivemind.queen.ticks.tools.quarantine import handle_quarantine_report, handle_tool_quarantine
from hivemind.queen.ticks.tools.requests import handle_tool_request
from hivemind.queen.ticks.tools.transfer import (
    drain_shipments,
    handle_basket_inbound,
    handle_basket_reply,
)
from hivemind.supervision.attendant import InboxItem
from waggle.ids import WardenId
from waggle.messages.basket import BasketAck, BasketChunk, BasketOffer, BasketRefuse

__all__ = [
    "QueenAnnouncer",
    "announce_current_tools",
    "drain_shipments",
    "forget_warden",
    "handle_tool_item",
    "stop_tools",
]


async def handle_tool_item(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Handle a tool or Basket message from a Warden directly; report whether it was one.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id (live).
        item: The ordered item.

    Returns:
        True when `item.payload` was a `ToolRequest`, `ToolQuarantine`, `ToolQuarantineReport`,
        `ToolPromoted`, `BasketOffer`, `BasketChunk`, `BasketAck` or `BasketRefuse` (handled
        either way); False otherwise, so the caller falls through to its next handler.
    """
    payload = item.payload
    if isinstance(payload, BasketOffer | BasketChunk):
        await handle_basket_inbound(deps, wardens, item)
        return True
    if isinstance(payload, BasketAck | BasketRefuse):
        await handle_basket_reply(deps, wardens, item)
        return True
    return (
        await handle_tool_request(deps, wardens, item)
        or await handle_tool_quarantine(deps, wardens, item)
        or await handle_quarantine_report(deps, wardens, item)
        or await handle_remote_promotion(deps, wardens, item)
    )
