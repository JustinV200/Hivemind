"""Receive a file in chunks: the put-file transfer table and its rules.

A Warden (the per-Cell supervisor) sends a file to the device as ``session.put_file`` chunks tied
together by a ``transfer_id`` (the envelope id of the chunk at offset 0). The Pollen Packet (the
thin gateway program on a device the Hive borrows) checks the first chunk against the rules of
gateway-contract section 7 (offset 0, a size under the family cap, room under the lease's scratch
quota and the free disk, at most eight open transfers per sender), appends each following chunk
only at the running length, never past the declared total and never past the room scratch has at
that moment, and on the final chunk verifies the length and the SHA-256 of the whole before
moving the file into place. The partial file lives in the lease's scratch directory,
never beside the target, so an abandoned or failed transfer leaves nothing outside scratch; a
transfer idle for a minute is discarded with its partial file. The packet never runs what it
wrote. Moving into place is the one step outside scratch for an outside target, and it happens
only after the path was recorded for the release to undo.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.executors.files, which owns the table; records outside-scratch targets through
    pollen.lease.restore and measures room through pollen.lease.quota; calls into waggle and the
    standard library only.

Key invariants:
    - A chunk is appended only at the transfer's running length, from the sender that began it,
      to the path it began with, and only while it keeps the file under its declared total and
      scratch under its quota.
    - A failed or refused transfer deletes its partial file and forgets the transfer.
    - The final file is verified (length and digest) before it replaces anything.
    - A target outside scratch is recorded in the lease table before it is changed.

See Also:
    - docs/waggle/gateway-contract.md sections 7 (chunk groups) and 9.2 (put-file).
    - pollen.executors.files for the executor that drives the table.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import stat
from pathlib import Path

from pollen.errors import PathRefusedError, PollenError, QuotaExceededError, TransferRefusedError
from pollen.executors.base import ExecutorDeps
from pollen.executors.paths import is_within
from pollen.lease.lease import DeviceLease
from pollen.lease.quota import check_room
from pollen.lease.restore import record_before_change
from waggle.envelope import Envelope
from waggle.ids import LeaseId, MessageId
from waggle.messages.base import CHUNK_GROUP_TIMEOUT_S, MAX_OPEN_CHUNK_GROUPS
from waggle.messages.session import SessionPutFile

MAX_PUT_FILE_BYTES = 1_073_741_824  # 1 GiB: this packet's cap on one transferred file.

__all__ = ["MAX_PUT_FILE_BYTES", "Transfer", "TransferTable"]


class Transfer:
    """One put-file transfer in progress.

    Owns mutable state (codingrules 8.5, documented): the running length, the running digest and
    the last-touched time, advanced by ``advance`` as each chunk lands.
    """

    def __init__(
        self, payload: SessionPutFile, sender: str, target: Path, temp: Path, now: float
    ) -> None:
        """Begin a transfer from its first chunk.

        Args:
            payload: The first chunk, which fixes the lease, the path and the total.
            sender: The envelope sender that began it; only it may continue it.
            target: The resolved path the file will be moved to.
            temp: The partial file inside the lease's scratch directory.
            now: The monotonic clock reading at the start.
        """
        self.transfer_id: MessageId = payload.transfer_id
        self.lease_id: LeaseId = payload.lease_id
        self.sender = sender
        self.target = target
        self.temp = temp
        self.total = payload.total_bytes
        self.written = 0
        self.touched = now
        self._digest = hashlib.sha256()

    def advance(self, chunk: bytes, now: float) -> None:
        """Account for ``chunk`` having been appended."""
        self._digest.update(chunk)
        self.written += len(chunk)
        self.touched = now

    def hexdigest(self) -> str:
        """Return the SHA-256 of everything appended so far, lowercase hex."""
        return self._digest.hexdigest()


class TransferTable:
    """The open put-file transfers, keyed by transfer id.

    Owns mutable state (codingrules 8.5, documented): ``_open`` is the set of transfers between
    their first chunk and their final one; it is touched only from the reader's task.
    """

    def __init__(self, deps: ExecutorDeps) -> None:
        """Bind the table to the shared collaborators.

        Args:
            deps: The lease table, layout and clock the rules need.
        """
        self._deps = deps
        self._open: dict[MessageId, Transfer] = {}

    async def accept(
        self, envelope: Envelope, payload: SessionPutFile, lease: DeviceLease, target: Path
    ) -> Transfer | None:
        """Apply one chunk of a transfer.

        Args:
            envelope: The chunk's envelope; its sender must be the transfer's.
            payload: The chunk.
            lease: The open lease the file is written for.
            target: The resolved path the chunk names.

        Returns:
            The finished, verified transfer when ``payload`` was the final chunk; else None.

        Raises:
            TransferRefusedError: A rule of gateway-contract section 7 was broken.
            PathRefusedError: The target is a directory.
            QuotaExceededError: There is no room, or the chunk could not be written.
        """
        await self._purge_idle()
        transfer = self._open.get(payload.transfer_id)
        if transfer is None:
            transfer = await self._start(envelope, payload, lease, target)
        try:
            self._check_continuation(transfer, envelope, payload, target)
            # Room is measured again for every chunk, against the bytes actually landing: the
            # start only saw the declared total, and scratch may have filled since (a command of
            # the lease writing there), so the quota holds whatever the sender declared.
            await asyncio.to_thread(
                check_room,
                Path(lease.scratch_root),
                lease.quota_bytes,
                len(payload.chunk),
                lease.lease_id,
            )
            await asyncio.to_thread(_append, transfer.temp, payload.chunk)
            transfer.advance(payload.chunk, self._deps.clock.monotonic())
            if payload.final:
                self._verify(transfer, payload)
                return self._open.pop(payload.transfer_id)
        except PollenError:
            await self.abort(payload.transfer_id)
            raise
        except OSError as error:
            await self.abort(payload.transfer_id)
            raise QuotaExceededError(
                f"Transfer {payload.transfer_id} could not be written to the lease's scratch."
            ) from error
        return None

    async def commit(self, transfer: Transfer, lease: DeviceLease, *, is_executable: bool) -> None:
        """Move a verified file to its path, recording an outside target first.

        Args:
            transfer: The finished transfer.
            lease: The lease it was written for.
            is_executable: Set the executable bit (a no-op on Windows).

        Raises:
            PathRefusedError: The file could not be put in place.
            QuotaExceededError: The target's prior state could not be recorded.
        """
        try:
            if not is_within(Path(lease.scratch_root), transfer.target):
                await record_before_change(
                    self._deps.leases, self._deps.layout, lease, transfer.target
                )
            await asyncio.to_thread(_move_into_place, transfer.temp, transfer.target, is_executable)
        except OSError as error:
            await asyncio.to_thread(transfer.temp.unlink, True)
            raise PathRefusedError(
                f"Transfer {transfer.transfer_id} could not be placed at its path."
            ) from error
        except PollenError:
            await asyncio.to_thread(transfer.temp.unlink, True)
            raise

    async def abort(self, transfer_id: MessageId) -> None:
        """Forget a transfer and delete its partial file."""
        transfer = self._open.pop(transfer_id, None)
        if transfer is not None:
            await asyncio.to_thread(transfer.temp.unlink, True)

    def drop_lease(self, lease_id: LeaseId) -> int:
        """Forget every transfer of ``lease_id``; its partial files go with the scratch.

        Args:
            lease_id: The lease being closed or released.

        Returns:
            How many transfers were dropped.
        """
        gone = [key for key, transfer in self._open.items() if transfer.lease_id == lease_id]
        for key in gone:
            del self._open[key]
        return len(gone)

    async def _start(
        self, envelope: Envelope, payload: SessionPutFile, lease: DeviceLease, target: Path
    ) -> Transfer:
        """Check the rules for a first chunk and begin the transfer."""
        if payload.offset != 0:
            raise TransferRefusedError(
                f"The first chunk of transfer {payload.transfer_id} must start at offset 0."
            )
        if payload.total_bytes > MAX_PUT_FILE_BYTES:
            raise TransferRefusedError(
                f"Transfer {payload.transfer_id} is {payload.total_bytes} bytes, over the "
                f"{MAX_PUT_FILE_BYTES} this packet accepts."
            )
        if (
            sum(1 for t in self._open.values() if t.sender == envelope.sender)
            >= MAX_OPEN_CHUNK_GROUPS
        ):
            raise TransferRefusedError(
                f"Sender {envelope.sender} already has {MAX_OPEN_CHUNK_GROUPS} open transfers."
            )
        scratch = Path(lease.scratch_root)
        if await asyncio.to_thread(target.is_dir):
            raise PathRefusedError(f"The path of transfer {payload.transfer_id} is a directory.")
        await asyncio.to_thread(
            check_room, scratch, lease.quota_bytes, payload.total_bytes, lease.lease_id
        )
        temp = scratch / f".put-{payload.transfer_id}.part"
        try:
            await asyncio.to_thread(temp.write_bytes, b"")
        except OSError as error:
            raise QuotaExceededError(
                f"Transfer {payload.transfer_id} could not start in the lease's scratch."
            ) from error
        transfer = Transfer(payload, envelope.sender, target, temp, self._deps.clock.monotonic())
        self._open[payload.transfer_id] = transfer
        return transfer

    def _check_continuation(
        self, transfer: Transfer, envelope: Envelope, payload: SessionPutFile, target: Path
    ) -> None:
        """Hold a chunk to its transfer: same sender, lease and path, at the running length."""
        if (envelope.sender, payload.lease_id, target) != (
            transfer.sender,
            transfer.lease_id,
            transfer.target,
        ):
            raise TransferRefusedError(
                f"A chunk of transfer {payload.transfer_id} does not match the one that began it."
            )
        if payload.offset != transfer.written:
            raise TransferRefusedError(
                f"A chunk of transfer {payload.transfer_id} starts at {payload.offset}, "
                f"not at the {transfer.written} bytes received so far."
            )
        # The total the first chunk declared is a ceiling on every later one: the wire holds each
        # chunk to its own total_bytes, so a sender who raised that figure chunk by chunk and never
        # sent the final one (the only one _verify sees) could otherwise grow the file without end.
        if payload.offset + len(payload.chunk) > transfer.total:
            raise TransferRefusedError(
                f"A chunk of transfer {payload.transfer_id} would take it to "
                f"{payload.offset + len(payload.chunk)} bytes, past the declared {transfer.total}."
            )

    def _verify(self, transfer: Transfer, payload: SessionPutFile) -> None:
        """Hold the finished file to its declared length and digest."""
        if transfer.written != transfer.total:
            raise TransferRefusedError(
                f"Transfer {transfer.transfer_id} ended at {transfer.written} bytes, not the "
                f"declared {transfer.total}."
            )
        if transfer.hexdigest() != payload.sha256:
            raise TransferRefusedError(f"Transfer {transfer.transfer_id} failed its digest check.")

    async def _purge_idle(self) -> None:
        """Discard transfers idle longer than the chunk-group timeout, partial files included."""
        now = self._deps.clock.monotonic()
        for key in [k for k, t in self._open.items() if now - t.touched > CHUNK_GROUP_TIMEOUT_S]:
            await self.abort(key)


def _append(temp: Path, chunk: bytes) -> None:
    """Append ``chunk`` to the partial file (blocking)."""
    with temp.open("ab") as handle:
        handle.write(chunk)


def _move_into_place(temp: Path, target: Path, is_executable: bool) -> None:
    """Replace ``target`` with the finished file (blocking), keeping an existing file's mode."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copymode(target, temp)
    if is_executable:
        mode = stat.S_IMODE(temp.stat().st_mode)
        os.chmod(temp, mode | ((mode & 0o444) >> 2))  # An x wherever there is an r.
    try:
        os.replace(temp, target)
    except OSError:
        # A different filesystem (scratch and target apart): copy it over instead.
        shutil.move(str(temp), str(target))
    with contextlib.suppress(OSError):
        temp.unlink(missing_ok=True)
