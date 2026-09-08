"""Provide MemoryTransport, the in-process Transport pair that moves frames through two queues.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and a
Transport carries its frames (the bytes the codec makes of one Envelope, the outer wrapper every
message travels in) between two bees. This pair is the transport for two ends in the same
process: the Warden (the always-on supervisor of one Cell, a unit of compute) and the Workers
(the subagents it spawns) that run inside the Queen's (the central orchestrator's) process in
phase 3, and every test that needs a transport without a socket. Each end owns an inbox queue
and the peer's inbox as its outbox; ``send`` encodes through this end's codec and puts the
BYTES on the outbox, ``receive`` takes bytes from the inbox and decodes them through this end's
codec, so the size, shape and signature checks are exactly those the WebSocket transport makes
and the memory transport is a faithful stand-in for it. Two hooks ship with it for tests:
``inject_frame`` delivers raw bytes to one end as if the peer had sent them, and ``drop``
simulates link loss on both ends.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Satisfies waggle.transport.base.Transport; used by
    hivemind.wardens for in-process Workers and by the waggle conformance suite; calls into
    waggle.codec and waggle.transport.base.

Key invariants:
    - The two ends exchange bytes, never Envelope objects: every frame passes through the
      sender's encode and the receiver's decode, so a frame the codec refuses is refused here.
    - Each queue carries frames in FIFO order and None as a wake-up sentinel (the peer closed,
      or the link dropped); nothing else is ever put on a queue.
    - Every await here is on an in-process queue: no timeout is needed because only the peer, a
      test hook or cancellation can end it, and cancellation is honoured.

See Also:
    - waggle.transport.base for the delivery contract and the close-code table.
    - waggle.transport.websocket for the transport this one stands in for.
    - docs/waggle/spec.md section 9 for the pair, inject_frame and drop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import CodecError, ConnectionLostError, SignatureError, TransportClosedError
from waggle.transport.base import CLOSE_NORMAL, close_code_for

__all__ = ["MemoryTransport"]


@dataclass
class _Link:
    """What both ends of one pair share: whether the link dropped and how the pair was closed."""

    is_dropped: bool = False  # drop() was called: every later send or receive on either end raises.
    close_code: int | None = None  # The code that ended the pair (CLOSE_NORMAL or a protocol code).


class MemoryTransport:
    """One end of an in-process pair; build two with ``pair`` and hand one to each bee.

    Concurrency model: one task sends and one receives per end; the queues are asyncio's own,
    so the two ends may live in different tasks of the same event loop but never in different
    threads or processes.
    """

    def __init__(
        self,
        codec: Codec,
        *,
        inbox: asyncio.Queue[bytes | None],
        outbox: asyncio.Queue[bytes | None],
        link: _Link,
    ) -> None:
        """Wire one end to its queues; callers use ``pair`` instead of this directly.

        Args:
            codec: Encodes what this end sends and decodes what it receives.
            inbox: The queue the peer's ``send`` puts frames on.
            outbox: The peer's inbox: where this end's ``send`` puts frames.
            link: The state shared with the peer end.
        """
        self._codec = codec
        self._inbox = inbox
        self._outbox = outbox
        self._link = link
        self._closed = False  # This end closed, refused a frame, or consumed the peer's sentinel.

    @classmethod
    def pair(cls, codec_a: Codec, codec_b: Codec) -> tuple[MemoryTransport, MemoryTransport]:
        """Create two connected ends, each with its own codec, ready to exchange frames.

        Args:
            codec_a: The first end's codec; signs its sends and verifies its receives per policy.
            codec_b: The second end's codec.

        Returns:
            ``(end_a, end_b)``: what ``end_a`` sends, ``end_b`` receives, and the reverse.
        """
        # Two queues, one per direction, so each end reads only what the peer wrote.
        a_to_b: asyncio.Queue[bytes | None] = asyncio.Queue()
        b_to_a: asyncio.Queue[bytes | None] = asyncio.Queue()
        link = _Link()
        return (
            cls(codec_a, inbox=b_to_a, outbox=a_to_b, link=link),
            cls(codec_b, inbox=a_to_b, outbox=b_to_a, link=link),
        )

    @property
    def is_connected(self) -> bool:
        """Whether frames can still flow: neither end closed and the link not dropped.

        Returns:
            True while ``send`` may succeed.
        """
        return not self._closed and not self._link.is_dropped and self._link.close_code is None

    @property
    def close_code(self) -> int | None:
        """The close code that ended the pair, for logs and the conformance suite.

        Returns:
            CLOSE_NORMAL after a clean close, the mapped protocol code after a refused frame
            (a protocol close overwrites a clean one, so the failure is what a log shows), or
            None while the pair is open or after a drop.
        """
        return self._link.close_code

    async def connect(self) -> None:
        """Do nothing: a pair is connected from construction and cannot be re-dialled.

        Returns:
            None. After ``close`` or ``drop`` a new pair is needed; nothing here revives one.
        """
        return None

    async def send(self, envelope: Envelope) -> None:
        """Encode ``envelope`` through this end's codec and put the frame on the peer's inbox.

        Args:
            envelope: A consistent Envelope; the codec signs it when configured to.

        Raises:
            FrameTooLargeError: The frame would exceed the codec's limit; nothing was queued.
            TransportClosedError: This end closed, or the peer closed cleanly.
            ConnectionLostError: The link dropped, or the peer closed after refusing a frame.
        """
        _require_open(self._closed, self._link)
        # Encode before queueing so a frame the codec refuses (too large) is never delivered:
        # the same order the WebSocket transport keeps.
        frame = self._codec.encode(envelope)
        self._outbox.put_nowait(frame)

    async def receive(self) -> AsyncGenerator[Envelope, None]:
        """Yield each frame the peer sent, decoded, until the pair ends.

        Frames already queued when either end closes cleanly are still delivered, as websockets
        drains its buffer after a close; a drop discards them.

        Returns:
            The envelopes in the order the peer sent them.

        Raises:
            ConnectionLostError: The link dropped, or the pair ended by a protocol close.
            CodecError: A frame failed to decode; the pair is closed with the mapped code,
                except for InvalidPayloadError, after which the pair stays open.
            SignatureError: A frame failed the codec's signature policy; closed with 1008.
        """
        while True:
            # Checked before every read (an earlier receive may have consumed drop()'s wake-up)
            # and after every wait (drop() may have woken this one), so a dropped link never
            # reads as a clean close.
            _raise_if_dropped(self._link)
            if self._closed:
                # Closed (by this end, or the peer's sentinel already consumed): only what is
                # already queued is delivered, without waiting for more.
                frame = self._inbox.get_nowait() if not self._inbox.empty() else None
            else:
                # In-process queue: resolves when the peer sends, closes or drops; no timeout
                # applies because cancellation is the only other way it can end.
                frame = await self._inbox.get()
                _raise_if_dropped(self._link)
            # The sentinel (or an empty queue after a close): nothing more will ever arrive.
            if frame is None:
                self._closed = True
                break
            yield self._decode(frame)
        _raise_unless_ended_cleanly(self._link)

    async def close(self) -> None:
        """End this end cleanly: the peer's ``receive`` drains what is queued, then ends.

        Returns:
            None. Calling it again, or after a drop or a refused frame, does nothing.
        """
        # Idempotent: a second close would put a second sentinel the peer never reads.
        if self._closed:
            return
        self._end(CLOSE_NORMAL)

    def inject_frame(self, frame: bytes) -> None:
        """Deliver raw bytes to this end as if the peer had sent them (a test hook that ships).

        Args:
            frame: The bytes ``receive`` will decode next; garbage, an oversized or a tampered
                frame exercises the codec's rejection paths through a real transport.
        """
        self._inbox.put_nowait(frame)

    def drop(self) -> None:
        """Simulate link loss on both ends: pending and later sends and receives raise.

        Returns:
            None. Nothing queued is delivered afterwards; a new pair is needed to talk again.
        """
        self._link.is_dropped = True
        # Wake a receive pending on either end; the drop flag is checked before the value is
        # read, so this None is never mistaken for the peer's clean close.
        self._inbox.put_nowait(None)
        self._outbox.put_nowait(None)

    def _decode(self, frame: bytes) -> Envelope:
        """Decode one frame, closing the pair with the mapped code when the codec refuses it."""
        try:
            return self._codec.decode(frame)
        except (CodecError, SignatureError) as exc:
            # None means the frame had a readable id: the caller answers with a control.error
            # and the pair stays open (spec section 7); anything else ends the pair.
            code = close_code_for(exc)
            if code is not None:
                self._end(code)
            raise

    def _end(self, code: int) -> None:
        """Close this end with ``code``, record it on the link and wake the peer's receive."""
        self._closed = True
        # A protocol close overwrites a clean one so the failure is what a log shows; a clean
        # close never hides a protocol code the other end already recorded.
        if code != CLOSE_NORMAL or self._link.close_code is None:
            self._link.close_code = code
        self._outbox.put_nowait(None)


def _require_open(closed: bool, link: _Link) -> None:
    """Raise the error the contract gives for a send on an end that cannot send."""
    # This end's own close comes first: it is the caller's action, so it outranks the state of
    # the link (codingrules 10: a full sentence naming the cause).
    if closed:
        raise TransportClosedError("This end of the memory pair is closed; nothing can be sent.")
    _raise_if_dropped(link)
    # The peer's clean close is final too; the peer's protocol close means it refused a frame,
    # which the WebSocket transport reports as a lost connection.
    if link.close_code == CLOSE_NORMAL:
        raise TransportClosedError("The peer end of the memory pair closed; nothing can be sent.")
    if link.close_code is not None:
        raise ConnectionLostError(
            f"The peer end of the memory pair closed with code {link.close_code} after refusing "
            "a frame."
        )


def _raise_if_dropped(link: _Link) -> None:
    """Raise ConnectionLostError once ``drop`` has been called on either end."""
    if link.is_dropped:
        raise ConnectionLostError("The link between the memory pair was dropped.")


def _raise_unless_ended_cleanly(link: _Link) -> None:
    """Turn how the pair ended into ``receive``'s outcome: return on clean, raise otherwise."""
    _raise_if_dropped(link)
    # A protocol close (either end refused a frame) is reported as a lost connection on every
    # receive after the one that raised the codec error, as the WebSocket transport does.
    if link.close_code != CLOSE_NORMAL:
        raise ConnectionLostError(
            f"The memory pair was closed with code {link.close_code} after a frame was refused."
        )
