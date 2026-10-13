"""Answer `session.put_file`, `session.get_file` and `session.delete_file` from a virtual disk.

The scripted device keeps its files as bytes in a dictionary keyed by absolute POSIX path. A write
arrives as chunks grouped by a transfer id (the first chunk's envelope id): the device checks each
chunk continues where the last stopped, and on the final chunk the length and SHA-256 of the whole,
stores the file and answers one `session.exit` correlated to the transfer id. A read streams the
file as `FILE` chunks and ends with the exit carrying its size and digest. A delete removes it. A
path outside scratch that no allowed scope covers draws `pollen.path_refused`, a missing file
`pollen.not_found`, as the gateway contract says (sections 9.2, 12 and 15). A script may make the
device lie about a digest or send a read's chunks out of order, to test that the Hive notices.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the scripted device.

Key invariants:
    - A write is visible in `state.files` only after its final chunk verified.
    - A refused chunk deletes the partial transfer and draws exactly one `control.error`.
    - Nothing here touches the real file system.

See Also:
    - docs/waggle/gateway-contract.md sections 9.2 and 12 for the behaviour mimicked.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from builders.swarm.device.context import DeviceContext
from builders.swarm.device.leases import LEASE_NOT_OPEN
from builders.swarm.device.state import DeviceLease

from waggle.envelope import Envelope
from waggle.ids import LeaseId, MessageId
from waggle.messages.base import MAX_CHUNK_BYTES, WaggleMessage
from waggle.messages.session.commands import SessionOutcome, SessionRequestKind, SessionStream
from waggle.messages.session.files import SessionDeleteFile, SessionGetFile, SessionPutFile
from waggle.messages.session.output import SessionExit, SessionOutput

PATH_REFUSED = "pollen.path_refused"
NOT_FOUND = "pollen.not_found"
TRANSFER_REFUSED = "pollen.transfer_refused"

__all__ = ["NOT_FOUND", "PATH_REFUSED", "TRANSFER_REFUSED", "FileService"]

Handler = Callable[[Envelope], Awaitable[None]]


class FileService:
    """The scripted device's file behaviour."""

    def __init__(self, ctx: DeviceContext) -> None:
        """Bind the service to a device context."""
        self._ctx = ctx
        self._partial: dict[str, bytearray] = {}  # transfer id -> bytes received so far.

    def handlers(self) -> dict[type[WaggleMessage], Handler]:
        """The request types this service answers, each with its handler."""
        return {
            SessionPutFile: self._put,
            SessionGetFile: self._get,
            SessionDeleteFile: self._delete,
        }

    async def _lease_for(self, request: Envelope, lease_id: LeaseId) -> DeviceLease | None:
        """Return the open lease, or answer `hive.session.lease_not_open` and return None."""
        lease = self._ctx.state.leases.get(lease_id)
        if lease is None or not lease.session_open:
            await self._ctx.error(request, LEASE_NOT_OPEN, "That lease has no open session.")
            return None
        return lease

    async def _put(self, request: Envelope) -> None:
        """Take one chunk of a write; on the final chunk verify and store the file."""
        chunk = request.payload
        assert isinstance(chunk, SessionPutFile)
        lease = await self._lease_for(request, chunk.lease_id)
        if lease is None:
            return
        state = self._ctx.state
        resolved = state.resolve(lease, chunk.path)
        if not state.is_reachable(lease, resolved):
            self._partial.pop(chunk.transfer_id, None)
            await self._ctx.error(request, PATH_REFUSED, "That path is out of the lease's reach.")
            return
        buffer = self._partial.setdefault(chunk.transfer_id, bytearray())
        if chunk.offset != len(buffer):
            self._partial.pop(chunk.transfer_id, None)
            await self._ctx.error(request, TRANSFER_REFUSED, "A chunk arrived out of order.")
            return
        buffer.extend(chunk.chunk)
        if chunk.final:
            await self._complete(
                request, chunk, resolved, bytes(self._partial.pop(chunk.transfer_id))
            )

    async def _complete(
        self, request: Envelope, chunk: SessionPutFile, resolved: str, data: bytes
    ) -> None:
        """Verify a finished write against its digest, store it and answer its one exit."""
        digest = hashlib.sha256(data).hexdigest()
        if digest != chunk.sha256 or len(data) != chunk.total_bytes:
            await self._ctx.error(request, TRANSFER_REFUSED, "The transfer's digest differs.")
            return
        self._ctx.state.files[resolved] = data
        reported = "0" * 64 if self._ctx.state.script.lie_about_sha256 else digest
        await self._ctx.reply(
            _correlated_to(request, chunk.transfer_id),
            _exit(
                chunk.lease_id, chunk.transfer_id, SessionRequestKind.PUT_FILE, len(data), reported
            ),
        )

    async def _get(self, request: Envelope) -> None:
        """Stream a file as `FILE` chunks and end with its size and digest."""
        asked = request.payload
        assert isinstance(asked, SessionGetFile)
        lease = await self._lease_for(request, asked.lease_id)
        if lease is None:
            return
        state = self._ctx.state
        resolved = state.resolve(lease, asked.path)
        if not state.is_reachable(lease, resolved):
            await self._ctx.error(request, PATH_REFUSED, "That path is out of the lease's reach.")
            return
        data = state.files.get(resolved)
        if data is None:
            await self._ctx.error(request, NOT_FOUND, "There is no such file.")
            return
        await self._stream(request, asked, data)

    async def _stream(self, request: Envelope, asked: SessionGetFile, data: bytes) -> None:
        """Send the chunks of a read, then its exit; out of order if the script says so."""
        starts = list(range(0, len(data), MAX_CHUNK_BYTES)) or [0]
        if self._ctx.state.script.reorder_file_chunks:
            starts.reverse()
        for start in starts:
            piece = data[start : start + MAX_CHUNK_BYTES]
            await self._ctx.reply(
                request,
                SessionOutput(
                    lease_id=asked.lease_id,
                    request_id=request.id,
                    stream=SessionStream.FILE,
                    chunk=piece,
                    offset=start,
                    final=start == starts[-1],
                ),
            )
        digest = (
            "0" * 64
            if self._ctx.state.script.lie_about_sha256
            else hashlib.sha256(data).hexdigest()
        )
        await self._ctx.reply(
            request,
            _exit(asked.lease_id, request.id, SessionRequestKind.GET_FILE, len(data), digest),
        )

    async def _delete(self, request: Envelope) -> None:
        """Remove a file and answer the DELETE_FILE exit, or say it is missing or out of reach."""
        asked = request.payload
        assert isinstance(asked, SessionDeleteFile)
        lease = await self._lease_for(request, asked.lease_id)
        if lease is None:
            return
        state = self._ctx.state
        resolved = state.resolve(lease, asked.path)
        if not state.is_reachable(lease, resolved):
            await self._ctx.error(request, PATH_REFUSED, "That path is out of the lease's reach.")
        elif resolved not in state.files:
            await self._ctx.error(request, NOT_FOUND, "There is no such file.")
        else:
            del state.files[resolved]
            await self._ctx.reply(
                request,
                _exit(asked.lease_id, request.id, SessionRequestKind.DELETE_FILE, None, None),
            )


def _correlated_to(request: Envelope, transfer_id: MessageId) -> Envelope:
    """Return an envelope whose id is the transfer's, so the reply correlates to the transfer."""
    return request.model_copy(update={"id": transfer_id})


def _exit(
    lease_id: LeaseId,
    request_id: MessageId,
    kind: SessionRequestKind,
    size: int | None,
    digest: str | None,
) -> SessionExit:
    """Build the `session.exit` that ends a file request."""
    return SessionExit(
        lease_id=lease_id,
        request_id=request_id,
        request_kind=kind,
        outcome=SessionOutcome.COMPLETED,
        exit_code=None,
        signal_name=None,
        duration_s=0.0,
        size_bytes=size,
        sha256=digest,
        reason="Scripted file operation.",
    )
