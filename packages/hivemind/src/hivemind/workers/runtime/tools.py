"""Give a running Worker its Tool channel: ask the Hive for a new tool and wait for the answer.

A Worker (a sub-agent doing one task) that lacks a tool asks for one with `request_tool`; the
request travels over Waggle (the bee-to-bee wire protocol) to its Warden (its per-Cell
supervisor), which relays it to the Queen, whose Royal Jelly Lab scaffolds, sandboxes and promotes
it (roadmap 9.7). `MailboxToolChannel` is `hivemind.workers.context.ToolChannel` over the Worker's
own `Mailbox`, mirroring `MailboxHoneyChannel`: `request` sends a `ToolRequest` and waits at most
`TOOL_REQUEST_TIMEOUT_S` for the `ToolPromoted` carrying the same `tool_id` or the `ErrorMessage`
correlated to the request's envelope; `resolve_promoted` and `resolve_error` are how the runtime's
receive dispatch hands an arriving answer to its waiter. A timeout is answered as a retryable
`ErrorMessage`, so a tool call never hangs and never raises for a slow Queen.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.runtime`. Built by
    `hivemind.workers.runtime.loop.WorkerRuntime`, which puts it on `WorkerContext.tools` and
    resolves every received `ToolPromoted` and `ErrorMessage` through it. Calls into
    `hivemind.common`, `hivemind.workers.context`, this package's `mailbox` and waggle only.

Key invariants:
    - A request's waiter is registered (under its tool id and its envelope id) before the envelope
      is sent, so an answer can never arrive ahead of the future it resolves.
    - `request` never waits past `TOOL_REQUEST_TIMEOUT_S` on the injected clock, and never raises
      for a missing answer; `resolve_*` never raise for an unmatched or late one (logged).
    - One answer resolves one request: whichever of a promotion and an error arrives first.

See Also:
    - docs/waggle/spec.md section 8.8 for ToolRequest and ToolPromoted.
    - hivemind.workers.runtime.honey for the channel this one mirrors.
    - hivemind.workers.tools.comb.request for the tool that calls it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from hivemind.workers.runtime.mailbox import Mailbox
from waggle.clock import Clock
from waggle.ids import MessageId, ToolId, WardenId
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest

# Five minutes: scaffolding is a model call of up to ten minutes on a slow local model, but a
# Worker's tool call should never stall its attempt longer than this; a late promotion still
# updates the bundle and is seen on a later request or round.
TOOL_REQUEST_TIMEOUT_S = 300.0
TIMEOUT_ERROR_CODE = "hive.tool.request_timeout"  # The code of the error a timeout is answered as.

__all__ = ["TIMEOUT_ERROR_CODE", "TOOL_REQUEST_TIMEOUT_S", "MailboxToolChannel"]

log = get_logger(__name__)


class MailboxToolChannel:
    """A Worker's `ToolChannel` over its own Mailbox: request a tool, await its promotion.

    Owns its mutable state in place (codingrules section 8.5): `_by_tool` holds one future per
    outstanding request keyed by tool id, and `_tool_of` maps each request's envelope id to that
    tool id so an error correlated to the envelope finds its waiter. `request` runs on a role's
    task while the runtime's tick calls `resolve_*`; both only touch these on one event loop.
    """

    def __init__(
        self,
        mailbox: Mailbox,
        clock: Clock,
        warden_id: WardenId,
        timeout_s: float = TOOL_REQUEST_TIMEOUT_S,
    ) -> None:
        """Wire the channel to the Worker's mailbox, clock and Warden.

        Args:
            mailbox: The Worker's one link to its Warden; every message here goes through it.
            clock: The injected time source a request's timeout sleeps on.
            warden_id: The Warden at the other end of the link (the hop's recipient), which a
                `ToolRequest` must name.
            timeout_s: How long a request waits for its answer.
        """
        self._mailbox = mailbox
        self._clock = clock
        self._warden_id = warden_id
        self._timeout_s = timeout_s
        self._by_tool: dict[ToolId, asyncio.Future[ToolPromoted | ErrorMessage]] = {}
        self._tool_of: dict[MessageId, ToolId] = {}

    @property
    def warden_id(self) -> WardenId:
        """The Warden this link leads to."""
        return self._warden_id

    async def request(self, request: ToolRequest) -> ToolPromoted | ErrorMessage:
        """Send `request` and wait, at most `timeout_s`, for its promotion or its refusal.

        Args:
            request: The tool wanted; the Warden relays it only when it names this Worker.

        Returns:
            The `ToolPromoted` with the request's tool id, or the `ErrorMessage` correlated to
            the request envelope; a retryable `hive.tool.request_timeout` error when neither
            arrived in time.

        Raises:
            waggle.errors.WaggleError: The link to the Warden is gone, so the request never left.
        """
        envelope = self._mailbox.envelope_for(request)
        future: asyncio.Future[ToolPromoted | ErrorMessage] = (
            asyncio.get_running_loop().create_future()
        )
        # Registered before the envelope leaves, so an answer can never outrun its own waiter.
        self._by_tool[request.tool_id] = future
        self._tool_of[envelope.id] = request.tool_id
        try:
            await self._mailbox.send_envelope(envelope)
            return await self._answer_or_timeout(future)
        finally:
            self._by_tool.pop(request.tool_id, None)
            self._tool_of.pop(envelope.id, None)

    def resolve_promoted(self, promoted: ToolPromoted) -> None:
        """Hand a received `ToolPromoted` to the request waiting for its tool id, if one is.

        Args:
            promoted: The announcement the Warden forwarded.
        """
        self._resolve(self._by_tool.get(promoted.tool_id), promoted, str(promoted.tool_id))

    def resolve_error(self, correlation_id: MessageId | None, error: ErrorMessage) -> None:
        """Hand a received `ErrorMessage` to the request whose envelope it answers, if one is.

        Args:
            correlation_id: The error envelope's own `correlation_id`: the request it refuses.
            error: The error itself.
        """
        tool_id = self._tool_of.get(correlation_id) if correlation_id is not None else None
        future = self._by_tool.get(tool_id) if tool_id is not None else None
        self._resolve(future, error, str(correlation_id))

    def _resolve(
        self,
        future: asyncio.Future[ToolPromoted | ErrorMessage] | None,
        answer: ToolPromoted | ErrorMessage,
        label: str,
    ) -> None:
        """Complete `future` with `answer`, or log a stray or late one."""
        if future is None or future.done():
            # A late answer (its request already timed out) or a stray: a peer's timing, logged.
            log.warning("workers.tools.unmatched_answer", ref=label)
            return
        future.set_result(answer)

    async def _answer_or_timeout(
        self, future: asyncio.Future[ToolPromoted | ErrorMessage]
    ) -> ToolPromoted | ErrorMessage:
        """Wait for `future` or the timeout, whichever comes first; an error on timeout."""
        timeout_task = asyncio.ensure_future(self._clock.sleep(self._timeout_s))
        waitables: set[asyncio.Future[Any]] = {future, timeout_task}
        try:
            # External await: the Queen scaffolds, quarantines and promotes (seconds to minutes);
            # past `timeout_s` the request gives up and the model reads why.
            done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap(timeout_task)
        if future in done:
            return future.result()
        return _timed_out(self._timeout_s)


def _timed_out(timeout_s: float) -> ErrorMessage:
    """Build the retryable error a request that heard nothing is answered as."""
    return ErrorMessage(
        code=TIMEOUT_ERROR_CODE,
        message=f"The Hive did not answer this tool request within {timeout_s:g}s; ask later.",
        failed_kind="tool.request",
        is_retryable=True,
    )
