"""Give a running Worker its Honey channel, and deposit each checkpoint's Handoff through it.

The Honey Store is the Hive's knowledge base, owned by the Queen (the orchestrator); a Worker (a
sub-agent doing one task) reaches it only over Waggle (the bee-to-bee wire protocol), through its
Warden (its per-Cell supervisor), which relays each message up and each answer back (roadmap step
7.8). `MailboxHoneyChannel` is `hivemind.workers.context.HoneyChannel` over the Worker's own
`Mailbox`: `query` sends a `HoneyQuery` and waits for the `HoneyResponse` whose envelope names the
query's envelope id, never longer than `HONEY_QUERY_TIMEOUT_S` (past it, an empty response says the
Queen did not answer, so a tool call never hangs); `deposit` sends a deposit's chunks in order;
`resolve` is how the runtime's receive dispatch hands an arriving response to its waiter. Its own
class, not more `Mailbox` methods, only because `Mailbox` is at codingrules 5.1's class limit.
`deposit_handoff` sends the Handoff a checkpoint just wrote (the document a bee writes before its
context is reset) as HANDOFF Nectar, keyed by its checkpoint event so the House Bee's later Bee
Bread copy of the same Handoff dedupes onto it (ADR-0035); a failure is logged, never raised,
because the Handoff is already durable in Bee Bread (the warm memory tier).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.runtime`. Built by
    `hivemind.workers.runtime.loop.WorkerRuntime`, which puts it on `WorkerContext.honey` and
    resolves every received `HoneyResponse` through it; `deposit_handoff` is called by
    `hivemind.workers.runtime.attempt` right after `memory.write_checkpoint`. Calls into
    `hivemind.common`, `hivemind.memory` (Handoff), `hivemind.workers.context`,
    `hivemind.workers.nectar`, this package's own `mailbox` and waggle only.

Key invariants:
    - A query's waiter is registered under its envelope id before the envelope is sent, so an
      answer can never arrive ahead of the future it resolves.
    - `query` never waits past `HONEY_QUERY_TIMEOUT_S` on the injected clock, and never raises
      for a missing answer; `resolve` never raises for an unmatched or late one (logged).
    - `deposit_handoff` never raises: every failure it can meet is logged with ids only.

See Also:
    - docs/waggle/spec.md section 8.7 for HoneyQuery, HoneyResponse and NectarDeposit.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the Handoff dedupe key.
    - hivemind.wardens.ticks.honey for the relay on the other end of this Worker's link.
    - hivemind.workers.runtime.mailbox for Mailbox, the link this channel sends over.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from hivemind.memory import Handoff
from hivemind.workers.context import WorkerContext
from hivemind.workers.nectar import DepositMeta, split_deposit
from hivemind.workers.runtime.mailbox import Mailbox
from waggle.clock import Clock
from waggle.errors import WaggleError
from waggle.ids import MessageId, TaskId
from waggle.messages import HandoffRef
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit, NectarKind
from waggle.messages.honey.exchange import MAX_TITLE_CHARS

# A query waits this long for the Queen: her own embedding call is bounded at ten seconds
# ([honey.retrieval] embed_timeout_s's default), and her tick may first finish an awake episode, so
# thirty seconds leaves room for both while never stalling a tool call for minutes.
HONEY_QUERY_TIMEOUT_S = 30.0
HANDOFF_MEDIA_TYPE = "application/json"  # A Handoff is deposited as its own JSON document.
HANDOFF_TITLE_PREFIX = "Handoff: "  # The browser lists a Handoff by what it was working towards.

__all__ = [
    "HANDOFF_MEDIA_TYPE",
    "HANDOFF_TITLE_PREFIX",
    "HONEY_QUERY_TIMEOUT_S",
    "MailboxHoneyChannel",
    "deposit_handoff",
]

log = get_logger(__name__)


class MailboxHoneyChannel:
    """A Worker's `HoneyChannel` over its own Mailbox: ask the Honey Store, deposit into it.

    Owns its own mutable state in place (codingrules section 8.5): `_pending`, one future per
    outstanding query, keyed by that query's own envelope id. `query` runs on a role's task while
    the runtime's tick calls `resolve`; both only touch `_pending` on one event loop.
    """

    def __init__(
        self, mailbox: Mailbox, clock: Clock, timeout_s: float = HONEY_QUERY_TIMEOUT_S
    ) -> None:
        """Wire the channel to the Worker's mailbox and clock.

        Args:
            mailbox: The Worker's one link to its Warden; every message here goes through it.
            clock: The injected time source a query's timeout sleeps on.
            timeout_s: How long a query waits for its answer; `HONEY_QUERY_TIMEOUT_S` unless a
                caller has a reason to wait differently.
        """
        self._mailbox = mailbox
        self._clock = clock
        self._timeout_s = timeout_s
        self._pending: dict[MessageId, asyncio.Future[HoneyResponse]] = {}

    async def query(self, query: HoneyQuery) -> HoneyResponse:
        """Send `query` and wait, at most `timeout_s`, for the Queen's matching response.

        Args:
            query: The question; the Warden relays it only when `requester` is this Worker and
                `task_id` its own task.

        Returns:
            The Queen's HoneyResponse; an empty one whose reason says she did not answer when
            none arrived in time.

        Raises:
            waggle.errors.WaggleError: The link to the Warden is gone, so the query never left.
        """
        envelope = self._mailbox.envelope_for(query)
        future: asyncio.Future[HoneyResponse] = asyncio.get_running_loop().create_future()
        # Registered before the envelope leaves, so an answer can never outrun its own waiter.
        self._pending[envelope.id] = future
        try:
            await self._mailbox.send_envelope(envelope)
            return await self._answer_or_timeout(future)
        finally:
            self._pending.pop(envelope.id, None)

    async def deposit(self, chunks: Sequence[NectarDeposit]) -> None:
        """Send every chunk of one deposit, in offset order.

        Args:
            chunks: One deposit's chunks, as `hivemind.workers.nectar.split_deposit` cut them.

        Raises:
            waggle.errors.WaggleError: The link to the Warden is gone or refused a frame.
        """
        # In order: intake refuses a chunk whose offset is not the running length.
        for chunk in chunks:
            await self._mailbox.send(chunk)

    def resolve(self, correlation_id: MessageId | None, response: HoneyResponse) -> None:
        """Hand a received HoneyResponse to the query waiting for it, if one still is.

        Args:
            correlation_id: The response envelope's own `correlation_id`: the query envelope it
                answers.
            response: The response itself.
        """
        future = self._pending.get(correlation_id) if correlation_id is not None else None
        if future is None or future.done():
            # A late answer (its query already timed out) or a stray: a peer's timing, logged.
            log.warning("workers.honey.unmatched_response", correlation_id=correlation_id)
            return
        future.set_result(response)

    async def _answer_or_timeout(self, future: asyncio.Future[HoneyResponse]) -> HoneyResponse:
        """Wait for `future` or the timeout, whichever comes first; an empty answer on timeout."""
        timeout_task = asyncio.ensure_future(self._clock.sleep(self._timeout_s))
        waitables: set[asyncio.Future[Any]] = {future, timeout_task}
        try:
            # External await: the Queen answers in milliseconds for full text and within her
            # embed timeout otherwise; past `timeout_s` the query gives up rather than hang.
            done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap(timeout_task)
        if future in done:
            return future.result()
        return _unanswered(self._timeout_s)


async def deposit_handoff(
    ctx: WorkerContext, task_id: TaskId, handoff: Handoff, ref: HandoffRef
) -> None:
    """Deposit a just-checkpointed Handoff as HANDOFF Nectar, when this Worker has a channel.

    Args:
        ctx: The Worker's context: its channel, Cell, id and tier.
        task_id: The task the checkpoint concerns.
        handoff: The Handoff document `memory.write_checkpoint` just stored.
        ref: What that write returned; its `event_id` keys the deposit and its dedupe.
    """
    if ctx.honey is None:
        return  # No runtime wired a channel: the Handoff stays in Bee Bread alone.
    # The checkpoint's own event id rides on the deposit, so intake keys it `handoff:<event id>`
    # and the House Bee's Bee Bread copy of the same Handoff dedupes onto this row (ADR-0035).
    meta = DepositMeta(
        kind=NectarKind.HANDOFF,
        media_type=HANDOFF_MEDIA_TYPE,
        title=f"{HANDOFF_TITLE_PREFIX}{handoff.goal}"[:MAX_TITLE_CHARS],
        task_id=task_id,
        cell_id=ctx.cell.id,
        worker_id=ctx.worker_id,
        observed_at=ref.written_at,
        clearance=handoff.clearance.to_wire(),
        origin_tier=ctx.cell.comb_shield.to_wire(),
        event_id=ref.event_id,
    )
    try:
        # Sends only, over the in-process link to the Warden: returns once every chunk is queued.
        await ctx.honey.deposit(split_deposit(handoff.model_dump_json().encode("utf-8"), meta))
    except (WaggleError, ValueError) as error:
        # The Handoff is already durable in Bee Bread, which the House Bee ripens later anyway:
        # a lost link or a refused frame costs this copy only. ValueError covers a bound the
        # deposit's own validation refused. Ids and the error's class only, never content.
        log.warning(
            "workers.honey.handoff_deposit_failed",
            worker_id=ctx.worker_id,
            task_id=task_id,
            event_id=ref.event_id,
            error=type(error).__name__,
        )


def _unanswered(timeout_s: float) -> HoneyResponse:
    """Build the empty response a query gets when the Queen did not answer in time."""
    return HoneyResponse(
        hits=(),
        token_count=0,
        is_truncated=False,
        filtered_count=0,
        reason=f"The Queen did not answer this Honey query within {timeout_s:g}s; nothing was "
        "retrieved.",
    )
