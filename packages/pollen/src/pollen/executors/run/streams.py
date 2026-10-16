"""Move an exec's bytes: its output frames out, and its stdin in.

A Warden (the per-Cell supervisor) sees an exec through two streams of frames. The program's
standard output and error are read as they arrive and sent as ``session.output`` chunks, each
stream with its own offset counting from zero and a final chunk that ends it before the one
``session.exit``; the total is capped, and the chunk that crosses the cap is cut at the cap so the
Pollen Packet (the thin gateway program on the device) never streams more than it promised. In the
other direction the Warden's stdin bytes are queued and written to the program in arrival order,
without ever blocking the reader that received them: a program that never reads its stdin must not
be able to wedge the packet. When no link is up a frame is dropped and the program keeps running,
because a dropped link stops the streaming and not the process.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.executors.run.runner; sends through the Reporter; calls into waggle and the standard
    library only.

Key invariants:
    - Offsets follow what was sent: a stream's chunks are contiguous from zero.
    - A stream's final chunk is sent once, however many times it is asked for.
    - Bytes past the cap are dropped, never sent, and the cap is reported to the caller.

See Also:
    - docs/waggle/gateway-contract.md section 11 for the output and stdin rules.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pollen.executors.base import Reporter
from pollen.executors.run.plan import ExecPlan
from waggle.messages.session import SessionOutput, SessionStdin, SessionStream

READ_CHUNK_BYTES = 65_536  # One pipe read: coalesces output without holding it back long.

__all__ = ["READ_CHUNK_BYTES", "OutputSink", "StdinFeeder"]


class OutputSink:
    """Numbers and sends one exec's output chunks, and notices when the cap is passed.

    Owns mutable state (codingrules 8.5, documented): the per-stream offsets, the bytes sent so
    far against the cap and the set of streams already ended.
    """

    def __init__(self, plan: ExecPlan, reporter: Reporter) -> None:
        """Bind the sink to one exec and the reporter its frames leave through.

        Args:
            plan: The exec whose output this is.
            reporter: Sends frames live; a frame with no link up is dropped.
        """
        self._plan = plan
        self._reporter = reporter
        self._offsets = {SessionStream.STDOUT: 0, SessionStream.STDERR: 0}
        self._sent = 0
        self._ended: set[SessionStream] = set()

    async def pump(
        self,
        stream: asyncio.StreamReader | None,
        kind: SessionStream,
        on_cap: Callable[[], None],
    ) -> None:
        """Read one pipe to EOF, sending each read as a chunk.

        Args:
            stream: The program's pipe; None when it has none.
            kind: STDOUT or STDERR.
            on_cap: Called when a chunk passes the output cap, to kill the group.
        """
        while stream is not None:
            # A local pipe: returns when the program writes, closes it, or is killed. The
            # watchdog bounds the program's life, so this needs no timeout of its own.
            data = await stream.read(READ_CHUNK_BYTES)
            if not data:
                return
            if await self.send(kind, data):
                on_cap()

    async def send(self, kind: SessionStream, data: bytes) -> bool:
        """Send ``data`` on ``kind`` up to the output cap.

        Args:
            kind: STDOUT or STDERR.
            data: The bytes just read.

        Returns:
            True if this chunk passed the cap (the part that fits was still sent): the caller
            must kill the group.
        """
        room = self._plan.max_output_bytes - self._sent
        passed = len(data) > room
        data = data[: max(0, room)]
        if data:
            # Reserved before the send, not after: the other stream's pump runs while this one
            # awaits the reporter, and would otherwise measure its room against a stale count
            # and overshoot the cap by a chunk of its own.
            self._sent += len(data)
            await self._frame(kind, data, final=False)
        return passed

    async def end(self, kind: SessionStream) -> None:
        """Send the empty final chunk that ends ``kind``, once."""
        if kind not in self._ended:
            self._ended.add(kind)
            await self._frame(kind, b"", final=True)

    async def end_all(self) -> None:
        """End both streams (also used when the program never started)."""
        await self.end(SessionStream.STDOUT)
        await self.end(SessionStream.STDERR)

    async def _frame(self, kind: SessionStream, data: bytes, *, final: bool) -> None:
        """Build and send one ``session.output`` frame, advancing the stream's offset."""
        offset = self._offsets[kind]
        self._offsets[kind] = offset + len(data)
        payload = SessionOutput(
            lease_id=self._plan.lease_id,
            request_id=self._plan.request_id,
            stream=kind,
            chunk=data,
            offset=offset,
            final=final,
        )
        # Dropped when no link is up: the process keeps running and the frame is discarded.
        await self._reporter.send(
            payload, to=self._plan.requester, correlation_id=self._plan.request_id
        )


class StdinFeeder:
    """Queues a program's stdin bytes and writes them in order, off the reader's path.

    Owns mutable state (codingrules 8.5, documented): the queue and the byte count written.
    """

    def __init__(self) -> None:
        """Start with an empty queue."""
        self._queue: asyncio.Queue[SessionStdin] = asyncio.Queue()
        self._written = 0

    def put(self, event: SessionStdin) -> None:
        """Queue one stdin event; never blocks the caller."""
        self._queue.put_nowait(event)

    async def run(self, process: asyncio.subprocess.Process) -> None:
        """Write queued bytes to ``process`` until cancelled, closing stdin on ``final``.

        Args:
            process: The running program; with no stdin pipe the bytes have nowhere to go.
        """
        while True:
            event = await self._queue.get()
            # The protocol writes in offset order, so a data chunk whose offset is not the
            # running length is dropped rather than guessed at; a bare close is always honoured.
            if process.stdin is None or (event.chunk and event.offset != self._written):
                continue
            try:
                if event.chunk:
                    process.stdin.write(event.chunk)
                    await process.stdin.drain()
                    self._written += len(event.chunk)
                if event.final:
                    process.stdin.close()
            except OSError:
                continue  # The program closed its stdin; the rest of its life is unaffected.
