"""Define Mailbox: one Worker's link to its Warden -- send, receive-or-heartbeat, ask a Question.

`Mailbox` owns the one `waggle.transport.base.Transport` end `hivemind.workers.runtime.deps.
RuntimeDeps.transport` hands a `hivemind.workers.runtime.loop.WorkerRuntime`. It gives the runtime
three things: `receive_task`/`take_received`, an owned, cached asyncio Task the runtime's own tick
races against its heartbeat deadline (codingrules section 11: "every task has an owner that awaits
or cancels it"); `heartbeat_task`/`clear_heartbeat`, the same shape for the heartbeat cadence;
and `ask`, which satisfies `hivemind.workers.context.QuestionChannel` structurally by sending a
`Question` and blocking only its own caller (a role's coroutine) on a private `asyncio.Future`
until `resolve_answer` delivers the matching `Answer` -- so a role blocked in `ask` never stops
the runtime's own tick loop from handling heartbeats, `TaskCancel` or anything else meanwhile.
Every frame this class receives passes through the transport's own codec, so an
`InvalidPayloadError` (a malformed frame with a readable id) leaves the pair open and this class
simply asks for the next one, while any other decode failure or a lost link ends the transport for
good -- both are reported to the caller as `None`, this module's one signal for "nothing more will
ever arrive here".

Fits into the Hive:
    Layer 4 (roles that do the work). Built and owned by `hivemind.workers.runtime.loop.
    WorkerRuntime` (roadmap step 3.15); also replaces `hivemind.workers.context.WorkerContext.
    asker` on the context the runtime hands to a role, since `Mailbox.ask` already satisfies
    `QuestionChannel`. Calls into `hivemind.common.logging`, `hivemind.common.tasks` (`reap`) and
    waggle only.

Key invariants:
    - `receive_task`/`heartbeat_task` each cache one in-flight `asyncio.Task` until the runtime
      consumes it (`take_received`/`clear_heartbeat`); calling either again before consuming
      returns the same cached Task rather than starting a second one.
    - `_next_envelope` never lets `InvalidPayloadError` end the transport: it logs and asks the
      underlying generator for the next frame instead, matching the Transport contract (spec
      section 7: the pair stays open after that one failure).
    - `resolve_answer` never raises: an Answer with no matching pending question (a stray, or one
      whose `ask` already returned) is logged and dropped, since a peer's timing is not this
      class's contract to enforce.
    - `aclose` reaps (cancels, then awaits) any in-flight receive/heartbeat Task before closing
      the transport, never merely cancels: the receive Task's own coroutine is suspended inside
      `self._inbox`'s `__anext__`, and closing the transport out from under it while it is still
      running is exactly what raises `RuntimeError("aclose(): asynchronous generator is already
      running")` (this dispatch's own shutdown-hygiene fix).

See Also:
    - .claude/codingrules.md section 11 for the structured-concurrency rule this module's cached
      tasks follow.
    - hivemind.common.tasks for `reap`, `aclose`'s own cancel-and-await primitive.
    - waggle.transport.base for the Transport contract this class's receive loop honours.
    - hivemind.workers.context for QuestionChannel, the Protocol `ask` satisfies structurally.
    - hivemind.workers.runtime.loop for WorkerRuntime, this class's one owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from hivemind.common.errors import InvariantViolationError
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, ConnectionLostError, InvalidPayloadError, SignatureError
from waggle.ids import MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.supervision import Answer, Question
from waggle.transport.base import Transport

log = get_logger(__name__)

__all__ = ["Mailbox"]


class Mailbox:
    """One Worker's link to its Warden: send, race receive against a heartbeat deadline, ask.

    Owns its transport end and every asyncio Task it spawns to read or wait on it (codingrules
    section 8.5: a class that owns mutable state documents it here). Concurrency model: `ask` may
    be called from a role's own task while the runtime's tick loop concurrently calls
    `receive_task`/`heartbeat_task`; both paths only ever touch this instance's own attributes,
    never each other's in-flight state, so the two interleave safely on one event loop.
    """

    def __init__(
        self, transport: Transport, hop: Hop, clock: Clock, heartbeat_interval_s: float
    ) -> None:
        """Wire a Mailbox to one transport end.

        Args:
            transport: This Worker's own end of its link to its Warden.
            hop: This Worker's address (`sender`) and its Warden's (`recipient`), stamped on
                every envelope `send` wraps.
            clock: Injected time source for every envelope id, `sent_at` timestamp and heartbeat
                sleep.
            heartbeat_interval_s: Seconds between one `heartbeat_task()` deadline and the next.
        """
        self._transport = transport
        self._hop = hop
        self._clock = clock
        self._heartbeat_interval_s = heartbeat_interval_s
        self._inbox: AsyncIterator[Envelope] = transport.receive()
        self._receive_task: asyncio.Task[Envelope | None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        # One pending Future per outstanding ask(), keyed by the Question's own id (never any
        # envelope's id -- it survives re-wrapping at each hop, per waggle.messages.supervision.
        # questions's own docstring).
        self._pending: dict[MessageId, asyncio.Future[Answer]] = {}

    def receive_task(self) -> asyncio.Task[Envelope | None]:
        """Return the in-flight receive Task, starting one if none is pending.

        Returns:
            A Task that resolves to the next Envelope, or None once the transport has ended
            (cleanly or otherwise -- see the module docstring).
        """
        if self._receive_task is None:
            self._receive_task = asyncio.ensure_future(self._next_envelope())
        return self._receive_task

    def take_received(self) -> Envelope | None:
        """Consume the finished receive Task's result and clear the cache.

        The caller must only call this once `receive_task()`'s Task is done (checked with
        `asyncio.wait`).

        Returns:
            The Envelope the peer sent, or None once the transport has ended.

        Raises:
            hivemind.common.errors.InvariantViolationError: Called with no receive Task in
                flight; the caller (`hivemind.workers.runtime.loop.WorkerRuntime._tick`) always
                calls `receive_task()` first in the same tick, so reaching this means that rule
                was broken.
        """
        task = self._receive_task
        if task is None:
            raise InvariantViolationError(
                "Mailbox.take_received called with no receive task in flight."
            )
        self._receive_task = None
        return task.result()

    def heartbeat_task(self) -> asyncio.Task[None]:
        """Return the in-flight heartbeat-deadline Task, starting one if none is pending.

        Returns:
            A Task that resolves once `heartbeat_interval_s` (fixed at construction by the
            caller's own cadence) has elapsed on the injected Clock.
        """
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.ensure_future(
                self._clock.sleep(self._heartbeat_interval_s)
            )
        return self._heartbeat_task

    def clear_heartbeat(self) -> None:
        """Drop the finished heartbeat-deadline Task so the next call starts a fresh one."""
        self._heartbeat_task = None

    async def send(
        self, payload: WaggleMessage, *, correlation_id: MessageId | None = None
    ) -> None:
        """Wrap `payload` in a fresh Envelope and hand it to the transport.

        Args:
            payload: The message to send.
            correlation_id: The request or antecedent this message answers or follows, per
                `waggle.envelope.MessageShape`'s rule for `payload`'s own kind; None for a
                request or an event with no antecedent.
        """
        envelope = wrap(payload, self._hop, clock=self._clock, correlation_id=correlation_id)
        await self._transport.send(envelope)

    async def ask(self, question: Question) -> Answer:
        """Send `question` and block only the caller until the matching Answer arrives.

        Satisfies `hivemind.workers.context.QuestionChannel` structurally. The runtime's own tick
        loop keeps handling heartbeats and control messages while a role is blocked here, because
        this coroutine only ever awaits its own private Future, never the mailbox as a whole.

        Args:
            question: The question to ask.

        Returns:
            The Answer whose `question_id` equals `question.question_id`.
        """
        future: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._pending[question.question_id] = future
        try:
            await self.send(question)
            return await future
        finally:
            self._pending.pop(question.question_id, None)

    def resolve_answer(self, answer: Answer) -> None:
        """Deliver `answer` to its matching pending `ask()`, if one is still waiting.

        Args:
            answer: An Answer envelope's payload, read off the mailbox's own receive loop.
        """
        future = self._pending.get(answer.question_id)
        if future is None or future.done():
            # A stray Answer (an old task's, or one whose ask() already returned): logged, not
            # raised, since a peer's timing is not this class's contract to enforce.
            log.warning("workers.mailbox.unmatched_answer", question_id=answer.question_id)
            return
        future.set_result(answer)

    async def aclose(self) -> None:
        """Reap any in-flight receive/heartbeat Task, then close this end of the transport.

        Idempotent, like `Transport.close`. Called once, by the runtime, as its last act before
        `run()` returns after `stop()`. The receive Task's own coroutine sits on `self._inbox`'s
        `__anext__`; reaping it (cancel, then await) before the transport closes is what keeps
        that async generator from being closed while it is still running (codingrules section 11).
        """
        if self._receive_task is not None:
            await reap(self._receive_task)
        if self._heartbeat_task is not None:
            await reap(self._heartbeat_task)
        await self._transport.close()

    async def _next_envelope(self) -> Envelope | None:
        """Return the next decoded Envelope, or None once nothing more will ever arrive.

        Recurses once per `InvalidPayloadError` (the pair stays open per the Transport contract),
        so this coroutine's own await point is always on the transport's receive generator, never
        on itself directly.
        """
        try:
            return await anext(self._inbox)
        except StopAsyncIteration:
            # A clean close from either side: receive() ends normally, nothing more will arrive.
            return None
        except InvalidPayloadError as error:
            log.warning("workers.mailbox.invalid_payload", error=str(error))
            return await self._next_envelope()
        except (ConnectionLostError, CodecError, SignatureError) as error:
            # A dropped link, or a protocol-level decode failure that closed the pair: neither is
            # recoverable on a MemoryTransport pair (a new pair is needed to talk again), so this
            # is reported the same way as a clean close.
            log.warning("workers.mailbox.transport_ended", error=str(error))
            return None
