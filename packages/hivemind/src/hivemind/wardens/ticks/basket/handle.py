"""Route one `basket.*` payload to the half of the Basket traffic it belongs to (roadmap 9.2a).

The `BASKET` autopilot action covers all four Basket messages, and they split by direction. From
the Queen, a `basket.offer` or `basket.chunk` is a blob arriving for this Warden's Cell
(`receive`); a `basket.ack` or `basket.refuse` answers a blob this Warden is shipping up
(`send`). Basket traffic is between a Warden and the Queen only, so anything arriving on a
sub-bee's link is dropped and logged: a Worker never talks to the Basket directly (ADR-0048).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Called by `hivemind.wardens.ticks.dispatch.act` for every
    `WardenAction.BASKET`. Calls into `receive`, `send` and `hivemind.common.logging`.

Key invariants:
    - Only items that arrived on the Queen link are handled; nothing is ever forwarded.
    - A payload of a type this module does not route does nothing.

See Also:
    - hivemind.wardens.ticks.basket.receive and .send for the two halves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.ticks.basket.receive import on_chunk, on_offer
from hivemind.wardens.ticks.basket.send import on_answer
from waggle.messages.basket import BasketAck, BasketChunk, BasketOffer, BasketRefuse

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["handle_basket_item"]

log = get_logger(__name__)


async def handle_basket_item(warden: Warden, item: InboxItem) -> None:
    """Handle one `basket.*` item from the Queen, or log and drop one from anywhere else.

    Args:
        warden: The owning Warden (read and written directly; see the ticks package docstring).
        item: The ordered inbox item; `principal` names the link it arrived on.
    """
    if item.principal != QUEEN_LINK:
        # A sub-bee has no business with the Basket's wire; its deposits travel in process.
        log.warning("wardens.basket.misdirected", kind=item.payload_kind, principal=item.principal)
        return
    payload = item.payload
    if isinstance(payload, BasketOffer):
        await on_offer(warden, item, payload)
    elif isinstance(payload, BasketChunk):
        await on_chunk(warden, item, payload)
    elif isinstance(payload, BasketAck | BasketRefuse):
        await on_answer(warden, payload)
