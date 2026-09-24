"""Define LinkReaders: hear every attached Warden link at once, one reader task per link.

The Queen (the Hive's single orchestrator) supervises Wardens (one per Cell, a unit of compute)
over one Waggle link each. Her tick used to hold a single `anext` future per link and take one
envelope per Warden per tick, so a tick that stalled -- a Virtual Cell provision awaited inline,
seconds on Docker and a minute on QEMU, or a long awake episode on a slow local model -- left
every Heartbeat sent meanwhile unread, and each later tick heard just one stale Heartbeat, judged
the Warden against it, and raised a fresh `CELL_UNREACHABLE` Alarm about a Warden that had been
heartbeating the whole time (a real run of 2026-09-24: five false Alarms). `LinkReaders` is the
fix's first half: every attached link gets its own reader task, started on attach and reaped on
detach and on stop, which reads its transport as frames arrive into that link's own bounded queue
and notes the newest Heartbeat it has heard as a `Pulse`: when it was sent, and the interval the
Warden declared on it (`Heartbeat.interval_s`), since every Warden heartbeats at its own cadence
(a Virtual Cell's in-Cell Warden every 15 s, a Swarm device at whatever its battery allows). The
tick `wait`s until anything is queued (or her stop flag or wake signal is set, or a quiet
heartbeat interval passes, so a tick still runs, and judges liveness, when every Warden has gone
silent and nothing else would wake her), `drain`s everything queued on every link into
`InboxItem`s for her Attendant, and judges liveness against `heard()` as well as what she has
handled, so a stall of her own tick is never mistaken for a silent Warden. The second half, never
letting a stale Heartbeat bring a Warden back online, is
`hivemind.queen.ticks.liveness.record_heartbeat`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's inbox
    sub-package: where a Warden's envelopes queue up before the Attendant triages them. Built once
    by `hivemind.queen.queen.Queen`; `hivemind.queen.attach` adds and removes links, the Queen's
    tick waits on and drains it, and `Queen.stop` closes it. Calls into `hivemind.common.tasks`
    (reap, reap_all, reaping), `hivemind.queen.inbox.weights` (to_inbox_item) and waggle only.

Key invariants:
    - At most `queue_size` (`LINK_QUEUE_SIZE` by default) envelopes wait per link. A full queue
      parks that link's reader, so the rest stay with its transport (backpressure): the Queen's
      memory never grows with a Warden's send rate. A Virtual Cell's transport is bounded too
      (`hivemind.queen.cell_gate.listener.FANOUT_QUEUE_SIZE`), so the wait reaches its socket.
    - `drain` takes everything queued on every link, and never more than `queue_size` from one
      link per call (nothing can be queued while its synchronous loop runs), so one flooding
      Warden adds at most that many items to a tick and every other link is drained in the same
      tick: no Warden is starved by another.
    - A reader task exists exactly while its link is attached: `add` starts it, `remove` and
      `aclose` cancel and reap it (codingrules section 11). None is ever left pending.
    - `wait` returns within `idle_s` on the caller's clock, whatever the links do: never a busy
      loop (one sleep per wait, and a wait per tick), never a tick starved of its timer.
    - A frame the codec refuses with `InvalidPayloadError` is skipped and reading resumes on a
      fresh `receive()` (the Transport contract: that pair stays open). A clean end, a lost link,
      any other decode failure or a signature failure ends the link with one `None` marker, which
      `drain` consumes without producing an item (the end-of-link handling the tick always had).
    - `heard()` never moves backwards for a link: it is the Pulse of the newest Heartbeat read,
      by `sent_at`, and carries that same Heartbeat's declared interval.

See Also:
    - .claude/codingrules.md section 11 for the structured-concurrency rules the readers follow.
    - hivemind.queen.ticks.liveness for record_heartbeat/check_liveness, which read `heard()`.
    - hivemind.queen.attach for attach_warden/detach_warden, which add and remove links.
    - waggle.transport.base for the Transport contract each reader reads.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from hivemind.common.tasks import reap, reap_all
from hivemind.queen.inbox.weights import to_inbox_item
from hivemind.supervision.attendant import InboxItem
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.errors import CodecError, ConnectionLostError, InvalidPayloadError, SignatureError
from waggle.ids import WardenId
from waggle.messages.supervision import Heartbeat
from waggle.transport.base import Transport

# Per link. At the real run's 0.5 s Heartbeat cadence this holds two minutes of one Warden's
# Heartbeats -- longer than a QEMU provision -- so an ordinary stall never parks a reader; at the
# 1 MiB frame cap it still bounds what one Warden can make the Queen hold.
LINK_QUEUE_SIZE = 256

__all__ = ["LINK_QUEUE_SIZE", "LinkReaders", "Pulse"]


@dataclass(frozen=True, slots=True)
class Pulse:
    """One Heartbeat as proof of life: when its Warden sent it, and the cadence it declared.

    Attributes:
        sent_at: The Heartbeat envelope's own `sent_at`: proof of life as of then, not of now.
        interval_s: The sender's own heartbeat interval (`Heartbeat.interval_s`), so the Queen
            judges each Warden against the cadence it actually keeps.
    """

    sent_at: datetime
    interval_s: float


@dataclass(slots=True)
class _Link:
    """One attached link's own reader state: its bounded queue, its task, the newest Heartbeat.

    Owns its own mutable state in place (codingrules section 8.5): `task` is set right after the
    reader it names is started (the reader needs this object first), and `pulse` advances as
    that reader hears newer Heartbeats.
    """

    queue: asyncio.Queue[Envelope | None]
    task: asyncio.Task[None] | None = None
    pulse: Pulse | None = None  # The newest Heartbeat read, by `sent_at`; None before any.

    def hear(self, pulse: Pulse) -> None:
        """Advance to `pulse` if it was sent after the newest one heard, never backwards."""
        if self.pulse is None or pulse.sent_at > self.pulse.sent_at:
            self.pulse = pulse


class LinkReaders:
    """One reader task per attached Warden link, each feeding its own bounded queue.

    Owns its own mutable state in place (codingrules section 8.5): `_links` gains and loses a
    link as Wardens attach and detach, and `_ready` is set by a reader that queued something and
    cleared by `drain` once every queue is empty.
    """

    def __init__(self, queue_size: int = LINK_QUEUE_SIZE) -> None:
        """Build a LinkReaders with no link yet; `add` starts each link's own reader.

        Args:
            queue_size: The most envelopes one link may have waiting, and so the most `drain`
                takes from one link per call. Must be >= 1; `LINK_QUEUE_SIZE` in production.

        Raises:
            ValueError: `queue_size` is below 1.
        """
        if queue_size < 1:
            raise ValueError(f"queue_size must be >= 1, got {queue_size}")
        self._queue_size = queue_size
        self._links: dict[WardenId, _Link] = {}
        # Set whenever a reader queues something; `wait` returns on it and `drain` clears it.
        self._ready = asyncio.Event()

    async def add(self, warden_id: WardenId, transport: Transport) -> None:
        """Start reading `transport` for `warden_id`, replacing any reader it already had.

        Args:
            warden_id: The attached Warden this link belongs to; every item it yields names it.
            transport: The Queen's own end of that Warden's link; only this reader reads it.
        """
        # A second attach of the same id must not leave the first reader running unowned.
        await self.remove(warden_id)
        link = _Link(queue=asyncio.Queue(maxsize=self._queue_size))
        # Owned: `remove`/`aclose` cancel and reap it; never a dropped handle (codingrules 11).
        link.task = asyncio.ensure_future(self._read(link, transport))
        self._links[warden_id] = link

    async def remove(self, warden_id: WardenId) -> None:
        """Stop reading `warden_id`'s link and drop what it had queued; a no-op if never added.

        Args:
            warden_id: The Warden being detached.
        """
        link = self._links.pop(warden_id, None)
        if link is not None and link.task is not None:
            await reap(link.task)

    async def aclose(self) -> None:
        """Stop every reader and forget every link: `Queen.stop`'s own reap."""
        links = tuple(self._links.values())
        self._links.clear()
        await reap_all(link.task for link in links if link.task is not None)

    async def wait(
        self, stop: asyncio.Event, wake: asyncio.Event, *, clock: Clock, idle_s: float
    ) -> bool:
        """Wait until any link has something queued, `wake` or `stop` is set, or `idle_s` passes.

        Args:
            stop: The Queen's own stop flag; once set, the tick has nothing left to do.
            wake: The Queen's wake signal (a goal request, a human message, a finished plan).
            clock: The Queen's own clock, so a test drives the quiet interval with a FakeClock.
            idle_s: The longest a wait lasts with nothing arriving: the heartbeat interval, so
                liveness is judged at least once per interval even when every Warden is silent.

        Returns:
            False once `stop` is set, so the caller's tick returns at once; True otherwise.
        """
        # Throwaway waiters, reaped on the way out even when the tick itself is cancelled mid-wait
        # (the finally below). In-process events and the Queen's own clock only: nothing external.
        waiters: tuple[asyncio.Future[Any], ...] = (
            asyncio.ensure_future(stop.wait()),
            asyncio.ensure_future(wake.wait()),
            asyncio.ensure_future(self._ready.wait()),
            asyncio.ensure_future(clock.sleep(idle_s)),
        )
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap_all(waiters)
        return not stop.is_set()

    def drain(self) -> list[InboxItem]:
        """Take everything queued on every link, in arrival order per link, as InboxItems.

        Returns:
            One InboxItem per envelope queued when this was called, at most `queue_size` per link;
            an end-of-link marker yields none.
        """
        items: list[InboxItem] = []
        for warden_id, link in self._links.items():
            # Bounded by the queue's own size: no reader runs while this synchronous loop does, so
            # nothing new can be queued behind what was there when it started.
            while not link.queue.empty():
                envelope = link.queue.get_nowait()
                if envelope is None:
                    continue  # The link ended; a later phase adds OFFLINE bookkeeping for this.
                items.append(to_inbox_item(envelope, warden_id))
        # Every queue is empty now: a reader that queues again sets it for the next tick.
        self._ready.clear()
        return items

    def heard(self) -> Mapping[WardenId, Pulse]:
        """Return the Pulse of each link's newest Heartbeat read so far, handled or not.

        Returns:
            A read-only snapshot, one entry per attached link that has delivered a Heartbeat.
        """
        return MappingProxyType(
            {wid: link.pulse for wid, link in self._links.items() if link.pulse is not None}
        )

    def queued(self, warden_id: WardenId) -> int:
        """Return how many envelopes wait on `warden_id`'s link right now; 0 when not attached.

        Args:
            warden_id: The attached Warden to look at.
        """
        link = self._links.get(warden_id)
        return link.queue.qsize() if link is not None else 0

    async def _read(self, link: _Link, transport: Transport) -> None:
        """Feed `transport` into `link.queue` until the link ends, then queue one None marker."""
        # One pass per receive() stream: a skipped frame ends a stream but not the pair.
        keep_reading = True
        while keep_reading:
            keep_reading = await self._pump(link, transport)
        # The link is over: the end-of-link marker `drain` consumes (module docstring).
        await link.queue.put(None)
        self._ready.set()

    async def _pump(self, link: _Link, transport: Transport) -> bool:
        """Read one `receive()` stream into `link.queue`; True when a skipped frame ended it."""
        stream = transport.receive()
        try:
            # External await per frame: a quiet Warden is not an error, so no timeout here; a dead
            # link is the transport's own keepalive to detect, and it ends this stream when it does.
            async for envelope in stream:
                if isinstance(envelope.payload, Heartbeat):
                    link.hear(
                        Pulse(sent_at=envelope.sent_at, interval_s=envelope.payload.interval_s)
                    )
                # Waits while the queue is full, leaving the rest with the transport (backpressure).
                await link.queue.put(envelope)
                self._ready.set()
        except InvalidPayloadError:
            return True  # That frame had a readable id: the pair stays open (Transport contract).
        except (ConnectionLostError, CodecError, SignatureError):
            return False  # A lost link or a refused frame ends it, exactly like a clean close.
        finally:
            await _close(stream)
        return False  # The peer closed cleanly: nothing more will ever arrive.


async def _close(stream: AsyncIterator[Envelope]) -> None:
    """Close an async-generator stream so its suspended frame never outlives its reader."""
    # Every Transport's receive() is an async generator (waggle.transport.base); a stream without
    # aclose has nothing to release.
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()
