"""Write, read and delete files through a gateway under test, in the chunks the contract names.

A file crosses the link in verified chunks (section 12 of the gateway contract): a write is a run
of `session.put_file` frames whose final chunk carries the whole file's SHA-256, a read is a
stream of `session.output` chunks that ends in one `session.exit`, and a delete is one request.
`put_messages` builds a write's chunks without sending them and `send_chunks` sends chunks as they
are given, so a test can send a transfer that is damaged on purpose (a wrong digest, a missing
first chunk) and see what the gateway does.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. A part of
    `contracts.gateway.Driver`; builds on `contracts.gateway.calls`.

Key invariants:
    - A transfer is one exchange: a refusal at any chunk stops the chunks after it.
    - The digest on a final chunk is the true one unless the test replaces it.

See Also:
    - docs/waggle/gateway-contract.md section 12 for the transfer rules checked with these.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from contracts.gateway.calls import REPLY_TIMEOUT_S, Calls, exit_of

from waggle.ids import MessageId
from waggle.messages.base import DEFAULT_MAX_OUTPUT_BYTES, MAX_CHUNK_BYTES
from waggle.messages.cell import LeaseOpened
from waggle.messages.session import (
    SessionDeleteFile,
    SessionExit,
    SessionGetFile,
    SessionOutput,
    SessionPutFile,
)

__all__ = ["FileCalls"]


class FileCalls(Calls):
    """The requests that move files into and out of a lease's scratch directory."""

    async def put_file(
        self, lease: LeaseOpened, path: str, data: bytes, *, chunk_bytes: int = MAX_CHUNK_BYTES
    ) -> SessionExit:
        """Write a file in chunks of at most `chunk_bytes` and return the gateway's exit."""
        transfer, messages = self.put_messages(lease, path, data, chunk_bytes=chunk_bytes)
        return await self.send_chunks(transfer, messages)

    def put_messages(
        self,
        lease: LeaseOpened,
        path: str,
        data: bytes,
        *,
        chunk_bytes: int = MAX_CHUNK_BYTES,
        digest: str | None = None,
    ) -> tuple[MessageId, list[SessionPutFile]]:
        """Build the chunks of a write; `digest` replaces the true one on the final chunk.

        Returns:
            The transfer id and the chunk messages, for `send_chunks`, so a test can send them
            damaged.
        """
        transfer = self.link.mint_id()
        pieces = [data[i : i + chunk_bytes] for i in range(0, len(data), chunk_bytes)] or [b""]
        whole = digest or hashlib.sha256(data).hexdigest()
        messages = [
            SessionPutFile(
                lease_id=lease.lease_id,
                transfer_id=transfer,
                total_bytes=len(data),
                path=path,
                chunk=piece,
                offset=index * chunk_bytes,
                final=index == len(pieces) - 1,
                sha256=whole if index == len(pieces) - 1 else None,
                is_executable=False,
                task_id=None,
                worker_id=None,
            )
            for index, piece in enumerate(pieces)
        ]
        return transfer, messages

    async def send_chunks(
        self, transfer: MessageId, messages: Sequence[SessionPutFile]
    ) -> SessionExit:
        """Send put-file chunks as they are given (even malformed ones) and return the exit."""
        async with self.link.exchange(transfer) as exchange:
            for message in messages:
                exchange.raise_if_failed()  # A refusal at one chunk stops the rest.
                await exchange.send(message, sender=self.warden)
            return exit_of(await exchange.reply(REPLY_TIMEOUT_S))

    async def get_file(
        self, lease: LeaseOpened, path: str, *, max_bytes: int | None = None
    ) -> tuple[bytes, list[SessionOutput], SessionExit]:
        """Read a file; return its bytes, the chunks it came in and the closing exit."""
        request = SessionGetFile(
            lease_id=lease.lease_id,
            path=path,
            task_id=None,
            worker_id=None,
            max_bytes=max_bytes or DEFAULT_MAX_OUTPUT_BYTES,
        )
        frames = [
            frame
            async for frame in self.link.stream(
                request, sender=self.warden, timeout_s=REPLY_TIMEOUT_S
            )
        ]
        chunks = [f.payload for f in frames if isinstance(f.payload, SessionOutput)]
        return b"".join(c.chunk for c in chunks), chunks, exit_of(frames[-1])

    async def delete_file(self, lease: LeaseOpened, path: str) -> SessionExit:
        """Delete a file and return the gateway's exit."""
        request = SessionDeleteFile(
            lease_id=lease.lease_id, path=path, task_id=None, worker_id=None
        )
        return exit_of(await self.ask(request))
