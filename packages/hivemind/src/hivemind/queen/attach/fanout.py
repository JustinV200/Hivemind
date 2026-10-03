"""Define FanoutTransport: one real reader of a remote Warden's connection, two consumers of it.

`waggle.transport.base.Transport`'s concurrency model is "one task sends and one task receives at
a time", and a single WebSocket connection has exactly one `recv()` to give out. The Queen's own
reader (`hivemind.queen.inbox.links.LinkReaders`) becomes a second reader the moment a Warden's
link is attached, and a gate that kept reading the same connection to answer a snapshot relay or
merge a trail segment would race it (the very first real Virtual Cell run crashed with
`websockets.exceptions.ConcurrencyError`). So the link the Queen is given is this class, built
around the connection: its `pump` is the one and only task that reads the connection's frames, and
every frame it reads is either answered inline (a snapshot relay request, a rollback request, a
trail segment: kinds the Queen's own dispatch must never see) or queued for the Queen to read
through `receive()`. At most `FANOUT_QUEUE_SIZE` frames wait unread; past that the pump parks and
leaves the socket unread, so a Queen that is not reading backs the Warden's own sends up
(backpressure) instead of her memory growing with them.

The pump reads the frame iterator it is given, not `transport.receive()`: a gate that has already
read a handshake from the connection (the Cell listener waits for `cell.ready` and a heartbeat; the
swarm listener's router reads the first frame to decide what the connection is) hands over the very
iterator it read them from, because a transport allows one `receive()` stream at a time.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.attach`.
    Built by `hivemind.queen.attach.serve.serve_warden` for every remote Warden's connection, the
    Virtual Cell listener's and the swarm gate's alike; the Queen reads it as the link's transport.
    Calls into waggle (envelope, errors, messages, transport) only.

Key invariants:
    - The pump is the only reader of the frame iterator it was given, from construction on.
    - The pump never raises: a lost connection, an undecodable frame or a failed signature is kept
      in `closed_by` and re-raised from `receive()` once the frames already queued are yielded.
    - At most `FANOUT_QUEUE_SIZE` envelopes wait unread; the end-of-stream marker always fits.
    - `send`, `close`, `connect` and `is_connected` are the real transport's, unchanged: a failed
      send raises exactly what the transport raises, for `send_guarded` to catch.

See Also:
    - hivemind.queen.attach.serve for the helper that builds and owns one of these.
    - hivemind.queen.inbox.links for the Queen's reader of this link's `receive()`.
    - waggle.transport.base for the Transport contract this implements structurally.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from waggle.envelope import Envelope
from waggle.errors import (
    CodecError,
    ConnectionLostError,
    FrameCeilingError,
    SignatureError,
    TransportClosedError,
)
from waggle.messages.cell.snapshot import CellRollbackRequest, CellSnapshotRequest
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.base import Transport

# The most envelopes one connection's fan-out holds for the Queen's reader before its pump stops
# reading the socket. Sixteen is websockets' own receive buffer (`max_queue`'s default, which
# `WebSocketServer` keeps): once both are full the library stops reading the TCP socket and the
# Warden's own sends block, backpressure all the way to it. The Queen's real per-link buffer is
# `hivemind.queen.inbox.links.LINK_QUEUE_SIZE`, behind this; more here would only hold frames she
# cannot read yet, and fewer would make every frame a lock-step hand-off between two tasks.
FANOUT_QUEUE_SIZE = 16

# The three kinds the pump answers itself: they are never meant for the Queen's bee-protocol
# dispatch, so they never reach her queue.
INLINE_KINDS = (CellSnapshotRequest, CellRollbackRequest, TrailSegmentSync)

# Marks "the pump will never put another envelope" on the fan-out's own queue; a private sentinel
# object (never an Envelope, so identity alone tells the two apart).
_FANOUT_DONE = object()

__all__ = ["FANOUT_QUEUE_SIZE", "INLINE_KINDS", "FanoutTransport"]


class FanoutTransport:
    """Wrap one accepted connection so the Queen's read and the gate's read agree.

    Implements `waggle.transport.base.Transport` structurally (`send`, `receive`, `close`,
    `is_connected`, `connect`, every one forwarded to or fed from the real transport), so a
    `WardenLink` and `Queen.attach_warden` need nothing more than that to accept it in place of the
    real transport.
    """

    def __init__(
        self,
        real: Transport,
        dispatch: Callable[[Envelope], Awaitable[None]],
        frames: AsyncIterator[Envelope] | None = None,
        claim: Callable[[Envelope], bool] | None = None,
    ) -> None:
        """Wrap `real`, starting `pump` at once so nothing else may ever read its frames.

        Args:
            real: The accepted connection; sends and the close go through it.
            dispatch: Answers an inline kind (`INLINE_KINDS`) on the pump's own task, bound to
                this specific connection by its gate.
            frames: The frame iterator to read, when the gate already holds one that has read the
                connection's handshake; None reads `real.receive()` afresh.
            claim: Takes a frame a Queen-side wait registered for (an Overwintering scrub's
                answer, `hivemind.queen.cell_gate.scrub`), on the pump's own task; a frame it
                takes is never queued for the Queen. None claims nothing.
        """
        self._real = real
        self._dispatch = dispatch
        self._claim = claim
        self._frames = frames if frames is not None else real.receive()
        self._queue: asyncio.Queue[Envelope | object] = asyncio.Queue()
        # One credit per envelope that may wait unread (module docstring: the bound lives here,
        # not in the queue, so the end-of-stream marker never waits for room, even while the pump
        # is being cancelled).
        self._room = asyncio.Semaphore(FANOUT_QUEUE_SIZE)
        self._closed_exc: Exception | None = None
        self.pump_task: asyncio.Task[None] = asyncio.ensure_future(self._pump())

    @property
    def is_connected(self) -> bool:
        """See `waggle.transport.base.Transport.is_connected`; forwarded to the real transport."""
        return self._real.is_connected

    @property
    def closed_by(self) -> Exception | None:
        """What ended the pump's read: a lost link, an undecodable frame or a failed signature."""
        return self._closed_exc

    async def connect(self) -> None:
        """See `waggle.transport.base.Transport.connect`; forwarded (already connected: a no-op)."""
        await self._real.connect()

    async def send(self, envelope: Envelope) -> None:
        """See `waggle.transport.base.Transport.send`; forwarded to the real transport directly.

        Deliberately not guarded here: this method IS the `Transport.send` this class implements,
        and `hivemind.queen.deps.send_guarded` (every caller that reaches this through a
        `WardenLink`) depends on it raising exactly what the Transport contract promises to catch
        and convert. Swallowing the exception here would make that catch dead code and leave every
        caller believing the send succeeded.
        """
        await self._real.send(envelope)

    async def receive(self) -> AsyncIterator[Envelope]:
        """Yield every envelope `pump` queued for the Queen; see `Transport.receive`.

        Raises:
            ConnectionLostError | CodecError | SignatureError: Whatever the pump's read raised,
                re-raised here once the queued items are exhausted, so a caller sees exactly the
                failure shape it would from the real transport.
        """
        while True:
            item = await self._queue.get()
            if item is _FANOUT_DONE:
                if self._closed_exc is not None:
                    raise self._closed_exc
                return  # A clean end: nothing more will ever arrive.
            assert isinstance(item, Envelope)  # noqa: S101 - only Envelope or the marker is queued.
            self._room.release()  # Taken off the fan-out: the pump may read one more frame.
            yield item

    async def close(self) -> None:
        """See `waggle.transport.base.Transport.close`; forwarded to the real transport directly."""
        await self._real.close()

    async def _pump(self) -> None:
        """The one and only reader of the frame iterator (module docstring).

        Never raises: a lost connection or a decode failure is recorded on `closed_by` (re-raised
        from `receive()`) rather than propagated out of this task, so a caller that only awaits
        `pump_task` sees a clean, normal return on every path.
        """
        try:
            async for envelope in self._frames:
                if isinstance(envelope.payload, INLINE_KINDS):
                    # Answered right here, inline: these kinds are never for the Queen's dispatch.
                    await self._dispatch(envelope)
                elif self._claim is None or not self._claim(envelope):
                    # Not an answer a Queen-side wait took (which goes no further). Parks while
                    # FANOUT_QUEUE_SIZE wait unread, the socket left unread with it: a Queen that
                    # is not reading backs the Warden's own sends up (backpressure).
                    await self._room.acquire()
                    self._queue.put_nowait(envelope)
        except (ConnectionLostError, CodecError, SignatureError, TransportClosedError) as exc:
            self._closed_exc = exc
        except FrameCeilingError:
            # The listener's own transport closed a flooding peer (code 4029) and has already
            # recorded it; to the Queen that is a link that ended, so nothing is re-raised to her.
            pass
        finally:
            # Never waits for room (the queue itself is unbounded): the stream still ends when
            # nobody reads it, and a cancelled pump never parks here in its own finally.
            self._queue.put_nowait(_FANOUT_DONE)
