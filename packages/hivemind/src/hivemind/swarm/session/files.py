"""Provide put_file, get_file and delete_file: move bytes to, from and out of a device's disk.

These are the file halves of `CellSession` for a Swarm device. Each resolves the path on the
device's own terms (`DevicePaths`), refuses one out of the lease's reach, states the capability the
request needs, asks the Guard and records `swarm.command_sent`, and only then speaks. A write
travels as `session.put_file` events of at most one wire chunk each, grouped by a transfer id (the
first chunk's own envelope id, which also correlates the device's single answer), the last chunk
carrying the SHA-256 of the whole; between chunks the sender looks for a refusal so one at chunk
one stops the rest. A read is a `session.get_file` request answered by `session.output` chunks on
the `FILE` stream, checked for order and for the length and digest the closing `session.exit`
reports, so a truncated or altered transfer raises instead of returning bytes. A delete is one
request and one `session.exit`. A file the device says is missing is `FileNotFoundError`, a path
it refuses is `PathNotAllowedError`, as for a local session.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Called by `PollenSession.put_file`, `get_file` and `delete_file`. Calls into the session's
    wire, guard and code tables, the gateway link's exchange and waggle's session messages.

Key invariants:
    - Nothing is sent before the Guard has authorised the request and the trail has recorded it.
    - No chunk carries more than `MAX_CHUNK_BYTES`, and a write's digest is of the bytes the
      caller gave, computed before the first chunk is sent.
    - A transfer whose length or digest the device reports differently, or whose chunks arrive out
      of order or past the size cap, raises `TransferIntegrityError`; its bytes are not returned.
    - File content and paths are never logged or put on the trail.

See Also:
    - docs/waggle/gateway-contract.md sections 9.2 and 12 for the transfer and path rules.
    - hivemind.swarm.gateway.exchange for the conversation a transfer runs in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hivemind.swarm.errors import DeviceRequestError, TransferIntegrityError
from hivemind.swarm.gateway.exchange import Exchange
from hivemind.swarm.session.exec import UNEXPECTED_REPLY_CODE
from hivemind.swarm.session.wire import (
    SessionWire,
    command_for,
    read_needs,
    translated,
    write_needs,
)
from waggle.envelope import Envelope
from waggle.ids import MessageId
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.session.commands import SessionRequestKind, SessionStream
from waggle.messages.session.files import SessionDeleteFile, SessionGetFile, SessionPutFile
from waggle.messages.session.output import SessionExit, SessionOutput

# Seconds to wait for each next frame of a file operation. A transfer is local-disk work on the
# device plus a few frames; two minutes is far beyond that and still bounds a device that stalls.
FILE_REPLY_TIMEOUT_S = 120.0

__all__ = ["FILE_REPLY_TIMEOUT_S", "delete_file", "get_file", "put_file"]


async def put_file(wire: SessionWire, path: Path, data: bytes) -> None:
    """Write `data` to `path` on the device, creating or overwriting it.

    Args:
        wire: The session's link, lease, guard and path rules.
        path: Where to write; relative to scratch when relative.
        data: The bytes to write.

    Raises:
        PathNotAllowedError: The path is outside scratch and every allowed path.
        CommandDeniedError: The Guard (or the device's own wall) refused the write.
        TransferIntegrityError: The device's report of what it stored differs from what was sent.
        GatewayUnavailableError: The link to the device is gone.
    """
    resolved = wire.paths.resolve(path)
    wire.paths.require_reach(resolved)
    needs = write_needs(wire, resolved)
    command = command_for(wire, "put_file", needs)
    await wire.guard.authorise(command)
    digest = hashlib.sha256(data).hexdigest()
    transfer_id = wire.link.mint_id()
    async with wire.link.exchange(transfer_id) as exchange:
        await wire.guard.record_sent(command, transfer_id)
        with translated(wire.error_context(path=resolved, needed=str(needs[0]))):
            for message in _put_chunks(wire, _Upload(resolved, data, digest, transfer_id)):
                exchange.raise_if_failed()  # A refusal at chunk one stops the rest being sent.
                await exchange.send(message, sender=wire.sender)
            reply = await exchange.reply(FILE_REPLY_TIMEOUT_S)
        _verify(_expect_exit(reply, SessionRequestKind.PUT_FILE), len(data), digest)


async def get_file(wire: SessionWire, path: Path) -> bytes:
    """Read and return the bytes at `path` on the device.

    Args:
        wire: The session's link, lease, guard and path rules.
        path: Where to read from; relative to scratch when relative.

    Returns:
        The file's contents, verified against the length and digest the device reported.

    Raises:
        PathNotAllowedError: The path is outside scratch and every allowed path.
        FileNotFoundError: The device has no such file.
        CommandDeniedError: The Guard (or the device's own wall) refused the read.
        TransferIntegrityError: The transfer arrived out of order, too long, or altered.
        GatewayUnavailableError: The link to the device is gone.
    """
    resolved = wire.paths.resolve(path)
    wire.paths.require_reach(resolved)
    needs = read_needs(wire, resolved)
    command = command_for(wire, "get_file", needs)
    await wire.guard.authorise(command)
    request = SessionGetFile(
        lease_id=wire.lease.id, path=resolved, task_id=wire.lease.task_id, worker_id=None
    )
    async with wire.link.exchange() as exchange:
        await wire.guard.record_sent(command, exchange.id)
        with translated(wire.error_context(path=resolved, needed=str(needs[0]))):
            await exchange.send(request, sender=wire.sender)
            return await _collect(exchange, request.max_bytes)


async def delete_file(wire: SessionWire, path: Path) -> None:
    """Remove the file at `path` on the device.

    Args:
        wire: The session's link, lease, guard and path rules.
        path: What to remove; relative to scratch when relative.

    Raises:
        PathNotAllowedError: The path is outside scratch and every allowed path.
        FileNotFoundError: The device has no such file.
        CommandDeniedError: The Guard (or the device's own wall) refused the delete.
        GatewayUnavailableError: The link to the device is gone.
    """
    resolved = wire.paths.resolve(path)
    wire.paths.require_reach(resolved)
    needs = write_needs(wire, resolved)
    command = command_for(wire, "delete_file", needs)
    await wire.guard.authorise(command)
    request = SessionDeleteFile(
        lease_id=wire.lease.id, path=resolved, task_id=wire.lease.task_id, worker_id=None
    )
    async with wire.link.exchange() as exchange:
        await wire.guard.record_sent(command, exchange.id)
        with translated(wire.error_context(path=resolved, needed=str(needs[0]))):
            await exchange.send(request, sender=wire.sender)
            reply = await exchange.reply(FILE_REPLY_TIMEOUT_S)
        _expect_exit(reply, SessionRequestKind.DELETE_FILE)


@dataclass(frozen=True, slots=True)
class _Upload:
    """One write ready to chunk: where it goes, what it holds and the id that groups its chunks."""

    resolved: str
    data: bytes
    digest: str
    transfer_id: MessageId


def _put_chunks(wire: SessionWire, upload: _Upload) -> Iterator[SessionPutFile]:
    """Yield the `session.put_file` events of one write, the last carrying the whole digest."""
    total = len(upload.data)
    # An empty file is still one (empty, final) chunk, so the device creates it.
    for start in range(0, total, MAX_CHUNK_BYTES) or range(1):
        piece = upload.data[start : start + MAX_CHUNK_BYTES]
        last = start + len(piece) >= total
        yield SessionPutFile(
            lease_id=wire.lease.id,
            transfer_id=upload.transfer_id,
            total_bytes=total,
            path=upload.resolved,
            chunk=piece,
            offset=start,
            final=last,
            sha256=upload.digest if last else None,
            is_executable=False,
            task_id=wire.lease.task_id,
            worker_id=None,
        )


async def _collect(exchange: Exchange, limit: int) -> bytes:
    """Gather a get-file transfer's chunks in order and check them against its closing exit."""
    received = bytearray()
    closing: SessionExit | None = None
    async for envelope in exchange.events(FILE_REPLY_TIMEOUT_S):
        payload = envelope.payload
        if isinstance(payload, SessionOutput) and payload.stream is SessionStream.FILE:
            # Each chunk must continue exactly where the last stopped, and the whole must stay
            # within the cap the request set, so a misbehaving device cannot grow the Hive's memory.
            if payload.offset != len(received) or len(received) + len(payload.chunk) > limit:
                raise TransferIntegrityError(
                    "A file transfer arrived out of order or past its size cap."
                )
            received.extend(payload.chunk)
        elif isinstance(payload, SessionExit):
            closing = _expect_exit(envelope, SessionRequestKind.GET_FILE)
    if closing is None:
        raise DeviceRequestError(UNEXPECTED_REPLY_CODE, "the transfer ended without an exit.")
    data = bytes(received)
    _verify(closing, len(data), hashlib.sha256(data).hexdigest())
    return data


def _expect_exit(envelope: Envelope, kind: SessionRequestKind) -> SessionExit:
    """Return the `session.exit` an operation ends in, or raise if the device sent anything else."""
    payload = envelope.payload
    if not isinstance(payload, SessionExit) or payload.request_kind is not kind:
        raise DeviceRequestError(
            UNEXPECTED_REPLY_CODE, f"the device did not end the {kind.value} request with its exit."
        )
    return payload


def _verify(exit_message: SessionExit, size: int, digest: str) -> None:
    """Raise `TransferIntegrityError` unless the exit reports exactly this length and digest."""
    if exit_message.size_bytes != size or exit_message.sha256 != digest:
        raise TransferIntegrityError(
            "The device's report of a file transfer differs from the bytes that moved."
        )
