"""Relay the Royal Jelly Lab's traffic between a Warden's sub-bees and the Queen (roadmap 9.6, 9.7).

A Worker that lacks a tool asks for one with `request_tool`; the request reaches its Warden over
its mailbox. `handle_tool_item` is the one `FORWARD_TOOL` handler. A `ToolRequest` is relayed only
when the first-hop identity holds -- the payload names the sending sub-bee, its own task, this
Warden and this Warden's Cell (docs/waggle/spec.md section 3: a sub-bee may never ask as another
bee or for another Cell) -- and then it goes one of two ways by scope. A HIVE-scope request is
forwarded to the Queen in a fresh envelope with the payload unchanged, the asker remembered by
tool id. A CELL-scope request is this Warden's own to fulfil: it starts its own promotion flow
(`cell_scope`). The Queen's answer comes back as a `tool.promoted` (the announcement of any
promotion, and the answer to a request) or a `control.error` (`failed_kind` `tool.request`). On a
`tool.promoted` the Warden installs the tool first (`install`: package verified resident, every
sub-bee's bundle re-attenuated) and only then forwards it to the asker, so a Worker is never told
of a tool it cannot yet call; a promotion whose package is not resident is never forwarded as a
success. A report or error answering this Warden's own sandbox request or promotion resolves the
future its background job awaits; a refusal of a promotion that arrives after the job stopped
waiting and installed the tool (`remote_registry` takes silence as acceptance) retracts it
(`install.retract_tool`), so the Warden's table never keeps what the Queen's registry refused.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Called by `hivemind.wardens.ticks.dispatch.act` for every
    `FORWARD_TOOL`. Calls into `pending`, `install`, `cell_scope`, `messages` and waggle; the
    Queen's half is the Queen's tool handlers (`hivemind.queen.ticks.tools`).

Key invariants:
    - A request is relayed or fulfilled only from the sending sub-bee's own link, and only when
      its payload names that sub-bee, its task, this Warden and this Cell; otherwise the asker is
      answered at once with `hive.tool.request_refused` and nothing leaves this Warden.
    - Upward kinds never arrive on the Queen link and downward kinds never on a sub-bee's: a
      message the wrong way is logged and dropped.
    - Nothing here raises for a stray message or a closed link (codingrules 8.8: a disconnected
      Warden keeps working); an unmatched reply is logged, an unreachable Queen answers the asker.
    - A payload is relayed unchanged, in a fresh envelope per hop; the correlation is this hop's.

See Also:
    - docs/waggle/spec.md sections 3 and 8.8 for relaying, first-hop identity and the tool family.
    - hivemind.wardens.ticks.honey for the Honey relay this one is shaped like.
    - hivemind.workers.runtime.tools for the Worker's own end of the conversation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.links import QUEEN_LINK, send_guarded
from hivemind.wardens.ticks.tools.cell_scope import start_cell_scope
from hivemind.wardens.ticks.tools.install import install_tool, retract_tool
from hivemind.wardens.ticks.tools.messages import (
    LOAD_REFUSED,
    NOT_HANDED_REASON,
    PROMOTED_KIND,
    REQUEST_KIND,
    REQUEST_REFUSED,
    answer_asker,
    tool_error,
)
from hivemind.wardens.ticks.tools.pending import Asker
from waggle.envelope import wrap
from waggle.ids import MessageId
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolPromoted, ToolQuarantineReport, ToolRequest
from waggle.messages.tool.authoring import ToolScope

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

REFUSED_REQUEST_REASON = (
    "This Warden relays a tool request only for the sub-bee that sent it, its own task, this "
    "Warden and this Warden's Cell; nothing was requested."
)
QUEEN_UNREACHABLE_REASON = "The Queen is unreachable from this Warden; the tool was not requested."

__all__ = [
    "QUEEN_UNREACHABLE_REASON",
    "REFUSED_REQUEST_REASON",
    "handle_tool_item",
]

log = get_logger(__name__)


async def handle_tool_item(warden: Warden, item: InboxItem, sub_bee: SubBee | None) -> None:
    """Relay or act on one `FORWARD_TOOL` item the way its direction allows, or log and drop it.

    Args:
        warden: The owning Warden (read and written directly; see the ticks package docstring).
        item: The ordered InboxItem; `principal` names the link it arrived on.
        sub_bee: The sub-bee `item` concerns, as the Warden's tick resolved it: the sender for an
            item from a sub-bee's link.
    """
    payload = item.payload
    from_sub_bee = sub_bee is not None and sub_bee.worker_id == item.principal
    if isinstance(payload, ToolRequest):
        # Upward: only from the sub-bee's own link.
        if sub_bee is None or not from_sub_bee:
            log.warning(
                "wardens.tool.misdirected", kind=item.payload_kind, principal=item.principal
            )
            return
        await _handle_request(warden, item, payload, sub_bee)
    elif item.principal != QUEEN_LINK:
        # Downward kinds (a promotion, a report, an error) come from the Queen alone.
        log.warning("wardens.tool.misdirected", kind=item.payload_kind, principal=item.principal)
    elif isinstance(payload, ToolPromoted):
        await _handle_promoted(warden, payload)
    elif isinstance(payload, ErrorMessage) and payload.failed_kind == REQUEST_KIND:
        await _forward_error(warden, item, payload)
    elif isinstance(payload, ToolQuarantineReport | ErrorMessage):
        # An answer to this Warden's own sandbox request or promotion: wake the job awaiting it.
        if not warden._tool_relay.resolve(_correlation(item), payload):
            await _late_reply(warden, item, payload)


async def _late_reply(
    warden: Warden, item: InboxItem, reply: ToolQuarantineReport | ErrorMessage
) -> None:
    """Act on a reply no job waits for: a refusal of an installed promotion retracts it."""
    if isinstance(reply, ErrorMessage) and reply.failed_kind == PROMOTED_KIND:
        announced = warden._tool_relay.take_announced(_correlation(item))
        if announced is not None:
            # The job took the Queen's silence as acceptance and installed the tool; her word,
            # however late, is the registry's, so the tool comes out again.
            await retract_tool(warden, announced, reply)
            return
    log.warning("wardens.tool.unmatched_reply", correlation_id=item.correlation_id)


async def _handle_request(
    warden: Warden, item: InboxItem, request: ToolRequest, sub_bee: SubBee
) -> None:
    """Refuse, fulfil (CELL scope) or forward (HIVE scope) one sub-bee's tool request."""
    asker = Asker(sub_bee.worker_id, MessageId(item.id))
    cell = warden._cell
    # First-hop identity (spec section 3): answered here, never forwarded, so a sub-bee can never
    # request as another bee, for another task or for a Cell this Warden does not supervise.
    if (
        request.worker_id != sub_bee.worker_id
        or request.task_id != sub_bee.task_id
        or request.warden_id != warden._warden_id
        or cell is None
        or request.cell_id != cell.id
    ):
        refusal = tool_error(REQUEST_REFUSED, REFUSED_REQUEST_REASON)
        await answer_asker(warden, asker.worker_id, refusal, asker.original_id)
        return
    if request.scope is ToolScope.CELL:
        await start_cell_scope(warden, request, asker)
        return
    await _forward_up(warden, request, asker)


async def _forward_up(warden: Warden, request: ToolRequest, asker: Asker) -> None:
    """Forward a HIVE-scope request to the Queen, remembering who asked."""
    deps = warden._deps
    # A fresh envelope for this hop (spec section 3's relaying rule): the payload is unchanged.
    envelope = wrap(request, deps.hop, clock=deps.clock)
    # Remembered before the envelope leaves, so the Queen's answer always finds its asker.
    warden._tool_relay.remember(request.tool_id, envelope.id, asker)
    if not await send_guarded(deps.queen_link, envelope):
        # Nothing will answer it: the asker hears so now rather than waiting out its timeout.
        warden._tool_relay.take_by_tool(request.tool_id)
        unreachable = tool_error(REQUEST_REFUSED, QUEEN_UNREACHABLE_REASON, retryable=True)
        await answer_asker(warden, asker.worker_id, unreachable, asker.original_id)


async def _handle_promoted(warden: Warden, message: ToolPromoted) -> None:
    """Install a promotion, then tell the sub-bee that asked for it (if one is waiting)."""
    installed = await install_tool(warden, message)
    asker = warden._tool_relay.take_by_tool(message.tool_id)
    if asker is None:
        return  # An announcement nobody here asked for: installed for whoever may call it.
    if installed:
        await answer_asker(warden, asker.worker_id, message, asker.original_id)
        return
    unhanded = tool_error(LOAD_REFUSED, NOT_HANDED_REASON, retryable=True)
    await answer_asker(warden, asker.worker_id, unhanded, asker.original_id)


async def _forward_error(warden: Warden, item: InboxItem, error: ErrorMessage) -> None:
    """Relay the Queen's refusal of a forwarded request to the sub-bee that made it."""
    asker = warden._tool_relay.take_by_envelope(_correlation(item))
    if asker is None:
        # Never forwarded by this Warden, already answered, or pushed out of a full table.
        log.warning("wardens.tool.unmatched_error", correlation_id=item.correlation_id)
        return
    await answer_asker(warden, asker.worker_id, error, asker.original_id)


def _correlation(item: InboxItem) -> MessageId | None:
    """Return the envelope id an item answers, typed as the id it is."""
    return MessageId(item.correlation_id) if item.correlation_id is not None else None
