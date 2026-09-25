"""Answer a bee's Honey query and take its Nectar deposits in, straight from the Queen's inbox.

The Honey Store is the Hive's knowledge base (roadmap phase 7), and the Queen (the orchestrator)
is the only bee that holds it. A Worker or a Warden reaches it over Waggle (the bee-to-bee wire
protocol): a `NectarDeposit` is one chunk of raw findings on its way in, a `HoneyQuery` a search
on its way out (roadmap step 7.8). `handle_honey_item` is the entry point
`hivemind.queen.ticks.liveness.handle_infrastructure_item` reaches for ahead of the autopilot
table, whose own fallback for an unrecognised payload would wake a model for routine traffic.
A deposit chunk goes to `NectarIntake.receive_chunk` with a `DepositSource` built from the
relaying Warden's own link -- its Cell's id, whether that Cell is borrowed, and its Comb Shield
tier (security tier) -- never from what the chunk claims; a refusal goes back as `control.error`
carrying the error's stable code. A query is answered as its requester: a Worker reads with the
default capabilities of its own task, goal, Cell and self, under its task's clearance
(data-sensitivity label), once the task is known and sits on the relaying Warden's own Cell; a
Warden reads as itself, under `[honey.clearance] default_label`. Either way the ceiling is the
lowest of what the query asked for, the principal's clearance and the Cell tier's read allowance,
and the reply is a `HoneyResponse` correlated to the query's envelope on the same link. With no
Honey Store wired (`QueenDeps.honey is None`) a query is answered empty with that reason and a
deposit is refused with `hivemind.honey_store.unavailable`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.ticks.liveness.handle_infrastructure_item` for every
    inbox item, before the Cell Wax check. Calls into `hivemind.brood_chamber` (TaskNotFoundError),
    `hivemind.cell` (CombShieldLevel, HoneyClearance), `hivemind.honey_store` (intake, retriever,
    scope and clearance rules), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.supervision.attendant` (InboxItem) and waggle only.

Key invariants:
    - The Queen awaits no model here beyond the retriever's own embedding call, which runs under
      `[honey.retrieval] embed_timeout_s`; intake calls no model at all.
    - A deposit's tier and borrowed flag come from the relaying Warden's `WardenLink.cell`, never
      from the sender (docs/waggle/spec.md section 8.7's receiver rule).
    - A Worker's query reads only its own task's scopes, and only when that task runs on the
      relaying Warden's own Cell; a Warden's query only as that Warden itself.
    - Every query gets exactly one `HoneyResponse` and every refused chunk exactly one
      `control.error` (spec section 3), both correlated to the item's envelope; nothing about a
      Night Veil Cell's traffic is logged, since its records never outlive the Cell (ADR-0035).
    - A Honey Store failure never escapes this handler: it becomes an empty response or a
      `control.error`, so it can never end the Queen's tick loop; nor does a reply to a Warden
      whose link closed since its message arrived (logged, dropped).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for intake, readers and the Night Veil
      rule this applies.
    - docs/waggle/spec.md section 8.7 for NectarDeposit, HoneyQuery and HoneyResponse.
    - hivemind.wardens.ticks.honey for the Warden-side relay this answers.
    - hivemind.queen.ticks.wax for the sibling handler this one's shape follows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.brood_chamber import TaskNotFoundError
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.guard import CapabilitySet
from hivemind.honey_store import (
    DepositSource,
    HoneyAccess,
    HoneyReader,
    HoneySearch,
    HoneyStoreError,
    NectarRejectedError,
    reader_ceiling,
    warden_read_capabilities,
    worker_read_capabilities,
)
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import Envelope, wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import IdKind, MessageId, WardenId, WorkerId
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS, ErrorMessage
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit

HONEY_UNAVAILABLE_CODE = "hivemind.honey_store.unavailable"  # Refuses a deposit with no store.
NO_HONEY_STORE_REASON = "No Honey Store is wired to this Hive, so nothing was searched."
NO_TASK_REASON = "A Worker's Honey query must name its own task; nothing was searched."
UNKNOWN_TASK_REASON = "The query's task is not known to the Queen, so nothing was searched."
FOREIGN_TASK_REASON = (
    "The query's task does not run on the relaying Warden's Cell, so nothing was searched."
)
WARDEN_MISMATCH_REASON = "A Warden may ask the Honey Store only as itself; nothing was searched."
STORE_FAILED_REASON = "The Honey Store failed while searching ({code}); nothing was returned."
_UNAVAILABLE_MESSAGE = "No Honey Store is wired to this Hive, so the deposit was not taken in."
_WORKER_PREFIX = f"{IdKind.WORKER.value}_"  # How a Worker's id reads on the wire.

__all__ = [
    "FOREIGN_TASK_REASON",
    "HONEY_UNAVAILABLE_CODE",
    "NO_HONEY_STORE_REASON",
    "NO_TASK_REASON",
    "STORE_FAILED_REASON",
    "UNKNOWN_TASK_REASON",
    "WARDEN_MISMATCH_REASON",
    "handle_honey_item",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Principal:
    """What a requester may read before the query's own ceiling applies: scopes and clearance."""

    capabilities: CapabilitySet  # Its default `honey:read` capabilities (ADR-0035).
    clearance: HoneyClearance  # Its own ceiling: a Worker's task clearance, a Warden's default.


async def handle_honey_item(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Handle a NectarDeposit or HoneyQuery InboxItem directly; report whether it was one.

    Args:
        deps: The Queen's collaborators; `deps.honey` is the Honey Store, or None.
        wardens: Every Warden currently attached, keyed by id; the reply goes back on the link
            the item arrived on.
        item: The ordered InboxItem; `item.principal` is the relaying Warden's own id and
            `item.id` the envelope id every reply is correlated to.

    Returns:
        True if `item.payload` was a NectarDeposit or a HoneyQuery (handled either way); False
        otherwise, so the caller falls through to its own ordinary dispatch.
    """
    payload = item.payload
    if not isinstance(payload, NectarDeposit | HoneyQuery):
        return False
    link = wardens.get(WardenId(item.principal))
    if link is None:
        # The relaying Warden detached since: no link to answer on, no Cell record to trust.
        log.debug("queen.honey.no_link", principal=item.principal, kind=item.payload_kind)
        return True
    if isinstance(payload, NectarDeposit):
        await _take_deposit(deps, link, item, payload)
    else:
        await _answer_query(deps, link, item, payload)
    return True


async def _take_deposit(
    deps: QueenDeps, link: WardenLink, item: InboxItem, deposit: NectarDeposit
) -> None:
    """Hand one chunk to intake as the relaying Warden's Cell; refuse it with control.error."""
    if deps.honey is None:
        # No Honey Store wired: every chunk is refused, so the sender never waits on a group.
        await _refuse(deps, link, item, HONEY_UNAVAILABLE_CODE, _UNAVAILABLE_MESSAGE)
        return
    cell = link.cell
    source = DepositSource(
        sender=item.principal,
        cell_id=cell.id,
        from_borrowed_cell=cell.is_borrowed,
        tier=cell.comb_shield,
    )
    try:
        # Local SQLite on the store's own thread (milliseconds), only on a final chunk; no model.
        await deps.honey.intake.receive_chunk(deposit, source)
    except NectarRejectedError as error:
        # Intake already recorded and logged it (never for Night Veil); the sender learns why.
        await _refuse(deps, link, item, error.code, str(error))
    except HoneyStoreError as error:
        # The store itself failed: the chunk group is gone either way, so the sender is told.
        if cell.comb_shield is not CombShieldLevel.NIGHT_VEIL:
            log.warning("queen.honey.deposit_failed", code=error.code, cell_id=cell.id)
        await _refuse(deps, link, item, error.code, str(error))


async def _answer_query(
    deps: QueenDeps, link: WardenLink, item: InboxItem, query: HoneyQuery
) -> None:
    """Search as the query's requester and send the response back on the same link."""
    response = await _search(deps, link, item, query)
    # A reply: correlated to the query envelope the Warden forwarded, which is all it matches on.
    envelope = wrap(response, link.hop, clock=deps.clock, correlation_id=MessageId(item.id))
    await _reply(link, envelope)


async def _search(
    deps: QueenDeps, link: WardenLink, item: InboxItem, query: HoneyQuery
) -> HoneyResponse:
    """Resolve the reader and run the search; an empty response with a reason when it cannot."""
    access = deps.honey
    if access is None:
        # No Honey Store wired (every pre-phase-7 Hive): say so rather than pretend no match.
        return _empty(NO_HONEY_STORE_REASON)
    principal = await _principal_for(deps, access, link, item, query)
    if isinstance(principal, str):
        return _empty(principal)  # The requester may not read here; the reason says why.
    # The ceiling is the lowest of what was asked, what the principal may see, and what the
    # Cell's tier may read (ADR-0035): a Night Veil reader never sees C2 whatever it asks.
    tier = link.cell.comb_shield
    requested = HoneyClearance.from_wire(query.max_clearance)
    reader = HoneyReader(
        requester=query.requester,
        capabilities=principal.capabilities,
        ceiling=reader_ceiling(requested, principal.clearance, tier, access.clearance.matrix),
        is_night_veil=tier is CombShieldLevel.NIGHT_VEIL,
    )
    search = HoneySearch(
        text=query.text,
        reader=reader,
        requested_scopes=query.scopes,
        max_hits=query.max_hits,
        max_tokens=query.max_tokens,
    )
    try:
        # External await: store reads on their own thread (milliseconds) plus, when an embedder
        # is bound, one embedding call bounded by [honey.retrieval] embed_timeout_s, past which
        # the retriever searches full text only rather than wait.
        return await access.retriever.search(search)
    except HoneyStoreError as error:
        # A store failure is the asker's empty answer, never the end of the Queen's tick; a
        # Night Veil reader's failure is not logged, as nothing of its traffic is (ADR-0035).
        if not reader.is_night_veil:
            log.warning("queen.honey.search_failed", code=error.code, cell_id=link.cell.id)
        return _empty(STORE_FAILED_REASON.format(code=error.code))


async def _principal_for(
    deps: QueenDeps, access: HoneyAccess, link: WardenLink, item: InboxItem, query: HoneyQuery
) -> _Principal | str:
    """Return what the requester may read, or the reason it may read nothing here."""
    # The wire allows a Worker or a Warden as requester (HoneyQuery's own validator); a Worker
    # reads through its task, a Warden only as itself.
    if query.requester.startswith(_WORKER_PREFIX):
        return await _worker_principal(deps, link, query)
    if query.requester != item.principal:
        return WARDEN_MISMATCH_REASON  # A Warden asks only as itself, on its own first hop.
    capabilities = warden_read_capabilities(link.cell.id, WardenId(query.requester))
    return _Principal(capabilities, HoneyClearance.from_wire(access.clearance.default_label))


async def _worker_principal(
    deps: QueenDeps, link: WardenLink, query: HoneyQuery
) -> _Principal | str:
    """Return a Worker's read set for its own task, or why it gets none."""
    if query.task_id is None:
        return NO_TASK_REASON  # A Worker only ever works a task; its query must name it.
    try:
        # The Brood Chamber's own store: in memory or local SQLite, milliseconds.
        task = await deps.chamber.get(query.task_id)
    except TaskNotFoundError:
        return UNKNOWN_TASK_REASON
    if task.cell_id != link.cell.id:
        # The relaying Warden vouches only for Workers on its own Cell.
        return FOREIGN_TASK_REASON
    capabilities = worker_read_capabilities(
        task.id, task.goal_id, link.cell.id, WorkerId(query.requester)
    )
    return _Principal(capabilities, task.spec.clearance)


async def _refuse(
    deps: QueenDeps, link: WardenLink, item: InboxItem, code: str, message: str
) -> None:
    """Answer a refused chunk with control.error: its stable code, never retryable alone."""
    error = ErrorMessage(
        code=code,
        message=message[:MAX_ERROR_MESSAGE_CHARS],
        failed_kind=item.payload_kind,
        # Only the whole deposit, resent from offset 0, could succeed: this chunk alone never.
        is_retryable=False,
    )
    # control.error is a reply to the chunk's own envelope (docs/waggle/spec.md section 7).
    envelope = wrap(error, link.hop, clock=deps.clock, correlation_id=MessageId(item.id))
    await _reply(link, envelope)


async def _reply(link: WardenLink, envelope: Envelope) -> None:
    """Send a reply down the relaying Warden's link; a link gone since is logged, not raised."""
    try:
        # The Warden's own link: an in-process queue or its WebSocket, queued at once and never
        # awaited on the far side, like every other reply the Queen's tick sends.
        await link.transport.send(envelope)
    except (TransportClosedError, ConnectionLostError):
        # The Warden detached after its message arrived: a benign race that must never end the
        # Queen's tick. Nothing about a Night Veil Cell's traffic is logged (ADR-0035).
        if link.cell.comb_shield is not CombShieldLevel.NIGHT_VEIL:
            log.warning("queen.honey.reply_undeliverable", warden_id=link.warden_id)


def _empty(reason: str) -> HoneyResponse:
    """Build a HoneyResponse with no hits and `reason`, for a query that never reached ranking."""
    return HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason=reason
    )
