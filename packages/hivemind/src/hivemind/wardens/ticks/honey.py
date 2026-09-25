"""Relay the Honey Store's traffic between a Warden's sub-bees and the Queen (roadmap step 7.8).

The Honey Store is the Hive's knowledge base, and only the Queen (the orchestrator) holds it. A
sub-bee (a Worker this Warden supervises) reaches it the only way it reaches anything beyond its
Cell: over Waggle (the bee-to-bee wire protocol), through this Warden. `handle_honey_item` is the
one `FORWARD_HONEY` handler. A sub-bee's `HoneyQuery` or `NectarDeposit` (a chunk of raw findings)
goes up to the Queen in a fresh envelope with its payload unchanged, once the first-hop identity
rule holds: the payload names the sending sub-bee and its own task (docs/waggle/spec.md section 3;
a sub-bee may never ask or deposit as another bee). For a query, `HoneyRelay` remembers the
forwarded envelope's id against the asking sub-bee and its original envelope's id, because the
Queen's `HoneyResponse` names its request only by `correlation_id`; the response is relayed back
to that sub-bee correlated to its own envelope. A Queen `control.error` about a relayed deposit is
logged at warning: a deposit is an event, and the sub-bee has nothing to wait for. A link that has
closed meanwhile is never a reason to end this Warden's tick (codingrules 8.8: a disconnected
Warden keeps working): a query the Queen cannot be reached for is answered at once with
`QUEEN_UNREACHABLE_REASON`, and an undeliverable chunk or answer is logged.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.
    ticks.assign`'s own module docstring for why), called by `hivemind.wardens.ticks.dispatch.act`
    for every `FORWARD_HONEY`. Calls into `hivemind.common.logging`, `hivemind.supervision.
    attendant` (InboxItem) and waggle only; the Queen's own half is `hivemind.queen.ticks.honey`.

Key invariants:
    - A payload is relayed unchanged, in a fresh envelope per hop; only the envelope's id,
      addresses and correlation change (spec section 3's relaying rule).
    - Upward kinds are relayed only from the sending sub-bee's own link, and only when the
      payload names that sub-bee and its own task; a response only ever travels down, to the
      sub-bee `HoneyRelay` remembered for it.
    - Nothing here raises for a stray message or a closed link: an unmatched response, a refused
      or misdirected payload, a response for a sub-bee that has since ended, and a send on a link
      that closed are logged and dropped (a query's asker is answered at once instead).
    - `HoneyRelay` never holds more than `MAX_PENDING_HONEY_QUERIES` entries.

See Also:
    - docs/waggle/spec.md sections 3 and 8.7 for relaying, first-hop identity and the honey family.
    - hivemind.queen.ticks.honey for the Queen's own handler of what this relays.
    - hivemind.workers.runtime.honey for the sub-bee's own end, MailboxHoneyChannel.
    - hivemind.wardens.ticks.questions for the Question/Answer relay this one is shaped like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import MessageId, WorkerId
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

# Forwarded queries still awaiting the Queen's answer. A sub-bee has at most one outstanding
# (`recall` waits for its answer), so more than this means answers stopped arriving and the
# oldest asker gave up long ago (its own channel times out): the oldest entry is dropped first.
MAX_PENDING_HONEY_QUERIES = 64
REFUSED_QUERY_REASON = (
    "This Warden relays a Honey query only for the sub-bee that sent it and its own task; "
    "nothing was searched."
)
QUEEN_UNREACHABLE_REASON = "The Queen is unreachable from this Warden; nothing was searched."
# A link that closed or dropped between a message arriving and its relay leaving.
_LINK_GONE = (TransportClosedError, ConnectionLostError)

__all__ = [
    "MAX_PENDING_HONEY_QUERIES",
    "QUEEN_UNREACHABLE_REASON",
    "REFUSED_QUERY_REASON",
    "ForwardedQuery",
    "HoneyRelay",
    "handle_honey_item",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ForwardedQuery:
    """Who asked a query this Warden forwarded, and on which of its own envelopes."""

    asker: WorkerId  # The sub-bee whose query it was.
    original_id: MessageId  # The sub-bee's own envelope id: the relayed answer's correlation.


class HoneyRelay:
    """Remember each forwarded HoneyQuery's asker until the Queen's answer comes back.

    Owns its own mutable state in place (codingrules section 8.5): `_pending`, the forwarded
    envelope id -> `ForwardedQuery` table, in insertion order so the oldest entry is the first
    dropped once the table is full.
    """

    def __init__(self) -> None:
        """Start with nothing forwarded."""
        self._pending: dict[str, ForwardedQuery] = {}

    def remember(self, forwarded_id: MessageId, asker: WorkerId, original_id: MessageId) -> None:
        """Record that `forwarded_id` carries `asker`'s query, first sent as `original_id`.

        Args:
            forwarded_id: The id of the envelope this Warden sent the Queen.
            asker: The sub-bee that asked.
            original_id: The id of the envelope the sub-bee sent this Warden.
        """
        self._pending[forwarded_id] = ForwardedQuery(asker=asker, original_id=original_id)
        # Oldest first: its asker's own wait ended long before the table filled.
        while len(self._pending) > MAX_PENDING_HONEY_QUERIES:
            del self._pending[next(iter(self._pending))]

    def take(self, forwarded_id: str | None) -> ForwardedQuery | None:
        """Pop and return who asked the query `forwarded_id` carried, if it is still known.

        Args:
            forwarded_id: A response envelope's own `correlation_id`.

        Returns:
            The asker and its original envelope id; None for no id, or one never forwarded or
            already answered.
        """
        if forwarded_id is None:
            return None
        return self._pending.pop(forwarded_id, None)

    def __len__(self) -> int:
        """Return how many forwarded queries still await an answer."""
        return len(self._pending)


async def handle_honey_item(warden: Warden, item: InboxItem, sub_bee: SubBee | None) -> None:
    """Relay one FORWARD_HONEY item the way its direction allows, or log and drop it.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        item: The ordered InboxItem; `principal` names the link it arrived on.
        sub_bee: The sub-bee `item` concerns, as the Warden's own tick resolved it: the sender
            for an item from a sub-bee's link.
    """
    payload = item.payload
    # Upward kinds must come from the sub-bee's own link; downward ones from the Queen's.
    from_sub_bee = sub_bee is not None and sub_bee.worker_id == item.principal
    upward = isinstance(payload, HoneyQuery | NectarDeposit)
    if upward != from_sub_bee:
        log.warning("wardens.honey.misdirected", kind=item.payload_kind, principal=item.principal)
        return
    if isinstance(payload, HoneyQuery) and sub_bee is not None:
        await _forward_query(warden, item, payload, sub_bee)
    elif isinstance(payload, NectarDeposit) and sub_bee is not None:
        await _forward_deposit(warden, payload, sub_bee)
    elif isinstance(payload, HoneyResponse):
        await _relay_response(warden, item, payload)
    elif isinstance(payload, ErrorMessage):
        # The Queen refused a relayed deposit: ids and the stable code only, never the content.
        log.warning(
            "wardens.honey.deposit_refused",
            warden_id=warden._warden_id,
            code=payload.code,
            correlation_id=item.correlation_id,
        )


async def _forward_query(
    warden: Warden, item: InboxItem, query: HoneyQuery, sub_bee: SubBee
) -> None:
    """Forward a sub-bee's own query to the Queen, remembering who asked; refuse any other."""
    if query.requester != sub_bee.worker_id or query.task_id != sub_bee.task_id:
        # First-hop identity (spec section 3): answered here, never forwarded, so a sub-bee can
        # never read with another bee's capabilities or another task's scope.
        await _answer_sub_bee(warden, sub_bee, _empty(REFUSED_QUERY_REASON), MessageId(item.id))
        return
    # A fresh envelope for this hop (spec section 3's relaying rule): the payload is unchanged.
    envelope = wrap(query, warden._deps.hop, clock=warden._deps.clock)
    # Remembered before the envelope leaves, so the Queen's answer always finds its asker.
    warden._honey_relay.remember(envelope.id, sub_bee.worker_id, MessageId(item.id))
    if not await _send_to_queen(warden, envelope):
        # Nothing will answer it: the asker hears so now rather than waiting out its timeout.
        warden._honey_relay.take(envelope.id)
        await _answer_sub_bee(warden, sub_bee, _empty(QUEEN_UNREACHABLE_REASON), MessageId(item.id))


async def _forward_deposit(warden: Warden, deposit: NectarDeposit, sub_bee: SubBee) -> None:
    """Forward one chunk of a sub-bee's own deposit to the Queen; drop any other."""
    if deposit.worker_id != sub_bee.worker_id or deposit.task_id != sub_bee.task_id:
        # First-hop identity: never forwarded as another bee's or another task's Nectar.
        log.warning("wardens.honey.deposit_not_relayed", worker_id=sub_bee.worker_id)
        return
    # Fire and forget, one fresh envelope per chunk: the Queen answers only a refusal, and a
    # chunk lost to a closed link leaves an incomplete group intake discards once it goes idle.
    await _send_to_queen(warden, wrap(deposit, warden._deps.hop, clock=warden._deps.clock))


async def _relay_response(warden: Warden, item: InboxItem, response: HoneyResponse) -> None:
    """Relay the Queen's response to the sub-bee that asked, correlated to its own envelope."""
    forwarded = warden._honey_relay.take(item.correlation_id)
    if forwarded is None:
        # Never forwarded by this Warden, already answered, or pushed out of a full table.
        log.warning("wardens.honey.unmatched_response", correlation_id=item.correlation_id)
        return
    sub_bee = warden._sub_bees.get(forwarded.asker)
    if sub_bee is None:
        # The asker has since ended; nothing is left to deliver the answer to.
        log.debug("wardens.honey.asker_gone", worker_id=forwarded.asker)
        return
    await _answer_sub_bee(warden, sub_bee, response, forwarded.original_id)


async def _answer_sub_bee(
    warden: Warden, sub_bee: SubBee, response: HoneyResponse, correlation_id: MessageId
) -> None:
    """Send `response` down `sub_bee`'s own link as the reply to its envelope `correlation_id`."""
    # This Warden's own hop to the sub-bee; the reply names the sub-bee's own envelope, the only
    # id its channel waits on (honey.response is a reply, so a correlation is mandatory).
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.identity.node_id
    )
    envelope = wrap(response, hop, clock=warden._deps.clock, correlation_id=correlation_id)
    try:
        # The sub-bee's in-process link: queued at once, read by its runtime's next tick.
        await sub_bee.link.send(envelope)
    except _LINK_GONE:
        # The sub-bee is ending and its link with it; nobody is left to read the answer.
        log.debug("wardens.honey.asker_link_gone", worker_id=sub_bee.worker_id)


async def _send_to_queen(warden: Warden, envelope: Envelope) -> bool:
    """Send `envelope` up the queen link; False, logged, when that link is gone."""
    try:
        # Queued at once on the in-process link, or handed to the WebSocket's own send buffer.
        await warden._deps.queen_link.send(envelope)
    except _LINK_GONE:
        # Codingrules 8.8: a disconnected Warden keeps working; this relay just has no Queen.
        log.warning("wardens.honey.queen_unreachable", kind=envelope.kind)
        return False
    return True


def _empty(reason: str) -> HoneyResponse:
    """Build the empty response a query gets when this Warden answers it itself."""
    return HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason=reason
    )
