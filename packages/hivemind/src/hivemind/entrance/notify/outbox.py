"""Provide PushOutbox: queue push notices and withdrawals, and deliver them off the caller's path.

The Queen tells the human's devices that something is waiting from inside her own tick, and an
enrolment flow does so from inside a request; neither may wait on a webhook that retries for half a
minute (ADR-0034's retries). ``PushOutbox`` is the queue between them and the ``PushDispatcher``
(the push channel's entry point): ``push`` and ``withdraw`` only enqueue and return, and ``run``,
owned by the Hive Entrance's task group, delivers each job with a bounded number in flight. Jobs
for one ``ref`` (the question, Alarm, goal or security event a notice points at) are delivered in
the order they were queued, so a question answered right after it was asked is withdrawn only
after its notice went out, never before. The audience of every notice is decided when it is
delivered, from the devices as the Entrance tables hold them then (``audience_for``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.notify``. Fed by
    ``PushHumanChannel`` (the Queen's ``HumanChannel``) and ``PushSecurityNotifier`` (enrolment's
    ``SecurityNotifier``); run by ``hivemind.entrance.runtime``. Calls into
    ``hivemind.entrance.push`` (the dispatcher and the audience rule) and the Entrance tables.

Key invariants:
    - ``push`` and ``withdraw`` never await: a full queue drops the job with a warning (the chat
      and the trail still hold what the notice pointed at).
    - Jobs sharing a ``ref`` are delivered strictly in queue order; at most
      ``MAX_DELIVERIES_IN_FLIGHT`` jobs run at once.
    - A delivery that fails is logged by ref and kind and never stops the outbox; no notice
      carries content, and nothing here logs a destination.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for what a notice is and who hears it.
    - hivemind.entrance.push.dispatch for the dispatcher every job ends in.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import deque
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.push import NoticeKind, PushDispatcher, PushNotice, audience_for
from waggle.clock import Clock
from waggle.ids import DeviceId

OUTBOX_CAPACITY = (
    1_024  # Queued jobs before new ones are dropped: far above any burst a human sees.
)
MAX_DELIVERIES_IN_FLIGHT = 8  # Jobs delivering at once; one stuck webhook never blocks the rest.

log = get_logger(__name__)

__all__ = ["MAX_DELIVERIES_IN_FLIGHT", "OUTBOX_CAPACITY", "DeviceSource", "PushOutbox"]


class DeviceSource(Protocol):
    """Every enrolled device as the Entrance tables hold them now (``EntranceStore``)."""

    async def list_devices(self) -> Sequence[EnrolledDevice]:
        """Return every device, whatever its status.

        Returns:
            The devices; the audience rule keeps only the APPROVED ones.
        """
        ...


@dataclass(frozen=True, slots=True)
class _Job:
    """One queued delivery: a notice of ``kind`` about ``ref``, or its withdrawal (kind None)."""

    ref: str
    kind: NoticeKind | None
    concerning: DeviceId | None = None
    submitter: DeviceId | None = None


class PushOutbox:
    """The queue of notices and withdrawals the Hive Entrance delivers in the background."""

    def __init__(
        self,
        dispatcher: PushDispatcher,
        devices: DeviceSource,
        clock: Clock,
        capacity: int = OUTBOX_CAPACITY,
    ) -> None:
        """Build an empty outbox; nothing is delivered until ``run``.

        Args:
            dispatcher: Where every job ends: the push channel's entry point.
            devices: The Entrance tables, read at delivery time for each notice's audience.
            clock: Mints each notice's id and time.
            capacity: Jobs held before new ones are dropped; >= 1.
        """
        self._dispatcher = dispatcher
        self._devices = devices
        self._clock = clock
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=max(1, capacity))
        # Bounds the deliveries in flight; each slot is released when its job ends.
        self._slots = asyncio.Semaphore(MAX_DELIVERIES_IN_FLIGHT)
        self._order = _RefOrder()

    def push(
        self,
        kind: NoticeKind,
        ref: str,
        *,
        concerning: DeviceId | None = None,
        submitter: DeviceId | None = None,
    ) -> bool:
        """Queue a "something is waiting" notice; never waits.

        Args:
            kind: What is waiting (never ``WITHDRAWN``: see ``withdraw``).
            ref: The id of the item it points at.
            concerning: For a security event or an Alarm, the device it is about, left out.
            submitter: For ``goal_completed``, the device that submitted the goal.

        Returns:
            True when queued; False when the outbox was full and the job was dropped.
        """
        return self._enqueue(_Job(ref, kind, concerning, submitter))

    def withdraw(self, ref: str) -> bool:
        """Queue the withdrawal of every notice about ``ref``; never waits.

        Args:
            ref: The question answered, the Alarm acknowledged, the request settled.

        Returns:
            True when queued; False when the outbox was full and the job was dropped.
        """
        return self._enqueue(_Job(ref, None))

    async def run(self) -> None:
        """Deliver queued jobs until cancelled, at most ``MAX_DELIVERIES_IN_FLIGHT`` at once."""
        async with asyncio.TaskGroup() as group:
            while True:
                # A slot first, then a job: nothing is ever taken off the queue unstarted.
                await self._slots.acquire()
                job = await self._queue.get()
                # Tasks start in creation order and take their ref's lock before any await, so
                # jobs sharing a ref run in queue order however many are in flight.
                group.create_task(self._deliver(job))

    async def join(self) -> None:
        """Wait until every job queued so far has been delivered (tests and shutdown)."""
        await self._queue.join()

    def _enqueue(self, job: _Job) -> bool:
        """Put ``job`` on the queue, or drop it with a warning when the queue is full."""
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            log.warning("entrance.push_dropped", ref=job.ref, kind=_kind_name(job))
            return False
        return True

    async def _deliver(self, job: _Job) -> None:
        """Deliver one job in its ref's turn; a failure is logged, never raised."""
        try:
            async with self._order.hold(job.ref):
                await self._send(job)
        except (HiveMindError, OSError, sqlite3.Error) as error:
            # A store or network failure loses this one notice; the item itself is still in
            # the chat or on the trail, where the device reads it at its next look.
            log.error(
                "entrance.push_failed",
                ref=job.ref,
                kind=_kind_name(job),
                error=type(error).__name__,
            )
        finally:
            self._slots.release()
            self._queue.task_done()

    async def _send(self, job: _Job) -> None:
        """Push a notice to its audience, or withdraw every notice about the ref."""
        if job.kind is None:
            # Latency: the dispatcher's own bounded deliveries to the original recipients.
            await self._dispatcher.withdraw(job.ref)
            return
        # Latency: one local read of the Entrance tables for the audience.
        devices = await self._devices.list_devices()
        audience = audience_for(
            job.kind, devices, concerning=job.concerning, submitter=job.submitter
        )
        notice = PushNotice.mint(job.kind, job.ref, self._clock)
        # Latency: every destination at once, each bounded by its channel's timeouts.
        await self._dispatcher.push(notice, audience)


class _RefOrder:
    """One FIFO lock per ref with jobs in flight; a ref's lock is dropped once nobody holds it."""

    def __init__(self) -> None:
        """Start with no ref in flight."""
        # ref -> (lock, the jobs holding or waiting for it); guarded by the event loop's thread.
        self._locks: dict[str, tuple[asyncio.Lock, deque[None]]] = {}

    @asynccontextmanager
    async def hold(self, ref: str) -> AsyncIterator[None]:
        """Hold ``ref``'s lock for the body; jobs for one ref queue in arrival order."""
        lock, users = self._locks.setdefault(ref, (asyncio.Lock(), deque()))
        users.append(None)
        try:
            async with lock:
                yield
        finally:
            users.pop()
            # The last user of a ref removes its entry, so the table never grows without bound.
            if not users:
                self._locks.pop(ref, None)


def _kind_name(job: _Job) -> str:
    """Name a job's kind for a log line: the notice kind, or ``withdrawn``."""
    return job.kind.value if job.kind is not None else NoticeKind.WITHDRAWN.value
