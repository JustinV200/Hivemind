"""Serve the session's file requests: put a file, get a file, delete a file.

A Warden (the per-Cell supervisor) moves whole files across the link to a Real Cell (an existing
device the Hive borrows for a task) through its Pollen Packet (the thin gateway program on the
device). Every path is resolved before it is judged, relative paths land under the lease's scratch
directory, and a path outside scratch needs the write (or read) capability *and*, for a change,
``cell:outside_scratch`` and a restore record written before the byte moves, so a release can put
it back (gateway-contract section 12). ``session.put_file`` chunks are applied by the transfer
table and the file is verified before it replaces anything; ``session.get_file`` streams the file
back as FILE chunks from a task of the packet's scope and ends with one exit carrying its size and
digest; ``session.delete_file`` removes exactly one file, never a directory. The packet never
runs what it wrote.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root
    and consulted by pollen.agent.dispatch; ends its transfers when pollen.agent.leases calls
    ``stop_lease``; calls into pollen.executors.transfers, pollen.lease.restore and waggle only.

Key invariants:
    - Every file request ends in exactly one ``session.exit`` or one ``control.error``.
    - A get is refused, not truncated, when the file is bigger than ``max_bytes``.
    - A delete removes one regular file; a missing one is ``pollen.not_found`` and a directory is
      ``pollen.path_refused``.
    - Nothing outside scratch changes before its prior state is recorded.

See Also:
    - docs/waggle/gateway-contract.md sections 9.2 (put, get, delete) and 12 (paths).
    - pollen.executors.transfers for the put-file rules.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import stat
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from pollen.errors import NotFoundError, PathRefusedError, PollenError, TransferRefusedError
from pollen.executors.base import ExecutorDeps, Prepared, error_payload, payload_of
from pollen.executors.paths import is_within, read_needs, resolve_path, write_needs
from pollen.executors.transfers import TransferTable
from pollen.lease.lease import DeviceLease
from pollen.lease.restore import record_before_change
from waggle.envelope import Envelope
from waggle.ids import LeaseId, MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.session import (
    SessionDeleteFile,
    SessionExit,
    SessionGetFile,
    SessionOutcome,
    SessionOutput,
    SessionPutFile,
    SessionRequestKind,
    SessionStream,
)

GET_CHUNK_BYTES = 131_072  # One FILE chunk: 128 KiB, comfortably under the 256 KiB wire bound.

log = logging.getLogger(__name__)

__all__ = ["FileExecutor"]


class FileExecutor:
    """Puts, gets and deletes files for a lease.

    Owns mutable state (codingrules 8.5, documented): the open put-file transfers and the
    get-file stream tasks per lease.
    """

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (
        SessionPutFile,
        SessionGetFile,
        SessionDeleteFile,
    )

    def __init__(self, deps: ExecutorDeps) -> None:
        """Bind the executor to the shared collaborators.

        Args:
            deps: State, leases, reporter, clock, platform shim, layout and task scope.
        """
        self._deps = deps
        self._transfers = TransferTable(deps)
        self._streams: dict[LeaseId, set[asyncio.Task[None]]] = {}
        self._preparers: dict[type[WaggleMessage], Callable[[Envelope], Prepared]] = {
            SessionPutFile: self._prepare_put,
            SessionGetFile: self._prepare_get,
            SessionDeleteFile: self._prepare_delete,
        }

    def prepare(self, envelope: Envelope) -> Prepared:
        """Resolve the request's path and name what it needs, changing nothing.

        Args:
            envelope: A ``session.put_file``, ``session.get_file`` or ``session.delete_file``.

        Returns:
            What it needs and how to run it.

        Raises:
            LeaseNotOpenError: The lease is unknown, releasing or its session was closed.
            PathRefusedError: The path cannot be resolved safely.
        """
        return self._preparers[type(envelope.payload)](envelope)

    async def stop_lease(self, lease_id: LeaseId, *, graceful: bool, reason: str) -> int:
        """End the transfers and streams of ``lease_id``; there is no process to kill.

        Args:
            lease_id: The lease being closed or released.
            graceful: Unused; a stream has nothing to wind down.
            reason: Unused; a stream ends without an exit of its own.

        Returns:
            Always 0: a file transfer is not a process.
        """
        self._transfers.drop_lease(lease_id)
        for task in self._streams.pop(lease_id, set()):
            task.cancel()
        return 0

    def _lease_and_path(self, lease_id: LeaseId, text: str) -> tuple[DeviceLease, Path]:
        """Return the open lease and the resolved path for a file request."""
        lease = self._deps.leases.require_session(lease_id)
        return lease, resolve_path(Path(lease.scratch_root), text, lease_id)

    # ──────────────────────────────────────────────────────────────────────────
    # put
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_put(self, envelope: Envelope) -> Prepared:
        """A put needs write on the path, and outside_scratch when it is outside scratch."""
        payload = payload_of(envelope, SessionPutFile)
        lease, target = self._lease_and_path(payload.lease_id, payload.path)
        needs = write_needs(target, Path(lease.scratch_root))

        async def run() -> None:
            finished = await self._transfers.accept(envelope, payload, lease, target)
            if finished is None:
                return  # A non-final chunk draws no answer unless it fails.
            await self._transfers.commit(finished, lease, is_executable=payload.is_executable)
            done = _completed(
                payload.lease_id,
                payload.transfer_id,
                SessionRequestKind.PUT_FILE,
                (finished.written, finished.hexdigest()),
            )
            await self._answer(envelope.sender, done)

        return Prepared(run=run, needs=needs, scratch_root=lease.scratch_root)

    # ──────────────────────────────────────────────────────────────────────────
    # get
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_get(self, envelope: Envelope) -> Prepared:
        """A get needs read on the path, in or out of scratch."""
        payload = payload_of(envelope, SessionGetFile)
        lease, source = self._lease_and_path(payload.lease_id, payload.path)

        async def run() -> None:
            size = await asyncio.to_thread(_regular_size, source, payload.lease_id)
            if size > payload.max_bytes:
                raise TransferRefusedError(
                    f"The file requested by {envelope.id} is {size} bytes, over the "
                    f"{payload.max_bytes} it may be."
                )
            task = self._deps.scope.spawn(self._stream(envelope, payload, source), name="get_file")
            tasks = self._streams.setdefault(payload.lease_id, set())
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        return Prepared(run=run, needs=read_needs(source), scratch_root=lease.scratch_root)

    async def _stream(self, request: Envelope, payload: SessionGetFile, source: Path) -> None:
        """Send the file as FILE chunks, then the one exit; report a failure as a control.error."""
        try:
            counted = await self._send_chunks(request, payload, source)
            done = _completed(payload.lease_id, request.id, SessionRequestKind.GET_FILE, counted)
            await self._answer(request.sender, done)
        except PollenError as error:
            await self._fail(request, error)
        except OSError:
            await self._fail(request, TransferRefusedError(f"File {request.id} could not be read."))

    async def _send_chunks(
        self, request: Envelope, payload: SessionGetFile, source: Path
    ) -> tuple[int, str]:
        """Read ``source`` and send each chunk, the last marked final; return size and digest."""
        digest = hashlib.sha256()
        offset = 0
        with source.open("rb") as handle:
            chunk = await asyncio.to_thread(handle.read, GET_CHUNK_BYTES)
            while True:
                # One chunk of look-ahead tells the sender which chunk is the last one.
                following = await asyncio.to_thread(handle.read, GET_CHUNK_BYTES)
                digest.update(chunk)
                message = SessionOutput(
                    lease_id=payload.lease_id,
                    request_id=request.id,
                    stream=SessionStream.FILE,
                    chunk=chunk,
                    offset=offset,
                    final=not following,
                )
                await self._deps.reporter.send(
                    message, to=request.sender, correlation_id=request.id
                )
                offset += len(chunk)
                if not following:
                    return offset, digest.hexdigest()
                chunk = following

    # ──────────────────────────────────────────────────────────────────────────
    # delete
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_delete(self, envelope: Envelope) -> Prepared:
        """A delete follows the write rules, including the record of prior bytes."""
        payload = payload_of(envelope, SessionDeleteFile)
        lease, target = self._lease_and_path(payload.lease_id, payload.path)
        needs = write_needs(target, Path(lease.scratch_root))

        async def run() -> None:
            await asyncio.to_thread(_regular_size, target, payload.lease_id)
            if not is_within(Path(lease.scratch_root), target):
                await record_before_change(self._deps.leases, self._deps.layout, lease, target)
            await asyncio.to_thread(target.unlink)
            done = _completed(payload.lease_id, envelope.id, SessionRequestKind.DELETE_FILE)
            await self._answer(envelope.sender, done)

        return Prepared(run=run, needs=needs, scratch_root=lease.scratch_root)

    # ──────────────────────────────────────────────────────────────────────────
    # answers
    # ──────────────────────────────────────────────────────────────────────────

    async def _answer(self, to: str, done: SessionExit) -> None:
        """Send the one ``session.exit`` that ends a file request."""
        await self._deps.reporter.send(done, to=to, correlation_id=done.request_id)

    async def _fail(self, request: Envelope, error: PollenError) -> None:
        """Send a ``control.error`` for a request whose work failed after it was accepted."""
        failed: ErrorMessage = error_payload(error, "session.get_file")
        await self._deps.reporter.send(failed, to=request.sender, correlation_id=request.id)


def _completed(
    lease_id: LeaseId,
    request_id: MessageId,
    kind: SessionRequestKind,
    counted: tuple[int, str] | None = None,
) -> SessionExit:
    """Build the COMPLETED exit of a file request; ``counted`` is (size, sha256) for put and get."""
    return SessionExit(
        lease_id=lease_id,
        request_id=request_id,
        request_kind=kind,
        outcome=SessionOutcome.COMPLETED,
        exit_code=None,
        signal_name=None,
        duration_s=0.0,
        size_bytes=None if counted is None else counted[0],
        sha256=None if counted is None else counted[1],
        reason="The file request completed.",
    )


def _regular_size(path: Path, lease_id: LeaseId) -> int:
    """Return the size of the regular file at ``path`` (blocking), or refuse.

    Raises:
        NotFoundError: Nothing is there.
        PathRefusedError: It is not a regular file.
    """
    try:
        status = path.stat()
    except FileNotFoundError as error:
        raise NotFoundError(
            f"A file named in a request for lease {lease_id} does not exist."
        ) from error
    except OSError as error:
        raise PathRefusedError(
            f"A path in a request for lease {lease_id} cannot be read."
        ) from error
    if not stat.S_ISREG(status.st_mode):
        raise PathRefusedError(f"A path in a request for lease {lease_id} is not a regular file.")
    return status.st_size
