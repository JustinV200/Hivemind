"""Speak the Warden's half of a conversation with a packet: open a lease, run, put, get, release.

An end-to-end test is a short conversation: the scripted Queen opens a lease, runs a program,
writes or reads a file, and releases the lease, then looks at what came back. Each of those is a
request and a handful of correlated frames, and spelling every field of every message in every
test would bury what the test is about. These helpers send the request the way the Warden does
(as the Cell's Warden, so the sender rules are exercised too), wait for the frames that end it,
and hand back the pieces in a small value object. They add no assertions of their own beyond
"the frame arrived"; the test decides what it means.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the pollen end-to-end tests; depends on
    waggle's message models and on queen_double.py.

Key invariants:
    - Requests are sent as the Cell's Warden unless a test says otherwise.
    - A helper waits for the frame that *ends* the request (an exit, a state, a release or a
      control.error), so the output it collected is complete.

See Also:
    - queen_double.py for the session these helpers speak through.
    - docs/waggle/gateway-contract.md section 9 for the requests themselves.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from frame_overrides import Overrides
from queen_double import QueenDouble, QueenSession

from waggle.envelope import Envelope
from waggle.ids import new_message_id
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.cell import CellRequest, CellTeardownRequest, LeaseOpened, ReleaseCause
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel, Urgency
from waggle.messages.session import (
    EnvVar,
    SessionExec,
    SessionExit,
    SessionGetFile,
    SessionOutput,
    SessionPutFile,
    SessionStream,
)

DEFAULT_TIMEOUT_S = 20.0  # The exec timeout the helpers ask for; tests that time out pass less.

__all__ = [
    "ExecOptions",
    "ExecResult",
    "FileResult",
    "Upload",
    "eventually",
    "exec_message",
    "get_file",
    "open_lease",
    "put_chunked",
    "put_file",
    "python_argv",
    "run_exec",
    "teardown",
]


@dataclass(frozen=True, slots=True)
class ExecOptions:
    """How an exec should run; the defaults ask for an ordinary short program."""

    cwd: str | None = None  # The working directory; the lease's scratch when None.
    env: Sequence[tuple[str, str]] = ()  # Variables overlaid on the packet's own environment.
    timeout_s: float = DEFAULT_TIMEOUT_S  # Seconds before the exec is timed out.
    has_stdin: bool = False  # Whether the test will stream stdin.
    max_output_bytes: int | None = None  # The output cap; the wire default when None.


@dataclass(frozen=True, slots=True)
class Upload:
    """A file to write onto the device: where, what, and how it is cut into chunks."""

    path: str  # The destination, absolute or relative to scratch.
    data: bytes  # The whole content.
    chunk_size: int = MAX_CHUNK_BYTES  # Bytes per ``session.put_file`` frame.
    is_executable: bool = False  # Ask for the executable bit.


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Everything an exec sent back."""

    request: Envelope  # The session.exec as sent.
    exit: SessionExit  # The one terminal event.
    outputs: tuple[SessionOutput, ...]  # Every chunk, in arrival order.

    def stream(self, which: SessionStream) -> bytes:
        """Return the bytes one stream carried, in offset order."""
        chunks = sorted((o for o in self.outputs if o.stream is which), key=lambda o: o.offset)
        return b"".join(chunk.chunk for chunk in chunks)

    @property
    def stdout(self) -> bytes:
        """What the program wrote to standard output."""
        return self.stream(SessionStream.STDOUT)

    @property
    def stderr(self) -> bytes:
        """What the program wrote to standard error."""
        return self.stream(SessionStream.STDERR)


@dataclass(frozen=True, slots=True)
class FileResult:
    """What a get-file sent back: the bytes and the exit that ended it."""

    exit: SessionExit
    content: bytes
    sha256: str


async def eventually(check: Callable[[], bool], wait_s: float = 10.0) -> None:
    """Wait until ``check()`` is true, polling every few milliseconds; fail after ``wait_s``.

    Raises:
        AssertionError: The condition did not become true in time.
    """
    try:
        async with asyncio.timeout(wait_s):
            # Polling disk state a packet changes: there is no event to wait on.
            while not check():  # noqa: ASYNC110
                await asyncio.sleep(0.01)
    except TimeoutError:
        raise AssertionError("The condition did not become true in time.") from None


def python_argv(code: str, *args: str) -> tuple[str, ...]:
    """Return the argv that runs ``code`` with this interpreter (a program every host has)."""
    return (sys.executable, "-c", code, *args)


async def open_lease(
    queen: QueenDouble,
    session: QueenSession,
    *,
    access: AccessLevel = AccessLevel.FULL,
    quota: int | None = None,
) -> LeaseOpened:
    """Ask for a lease as the Warden and return the packet's ``cell.lease_opened``."""
    request = CellRequest(
        cell_id=queen.cell_id,
        needs=None,
        holder=queen.warden_id,
        task_id=None,
        access_level=access,
        lifetime_s=None,
        scratch_quota_bytes=quota,
        reason="A test needs the device.",
    )
    sent = await session.send(request, sender=queen.warden_id)
    opened = await session.expect(LeaseOpened, where=lambda e: e.correlation_id == sent.id)
    return opened.payload


def exec_message(
    lease_id: str, argv: Sequence[str], options: ExecOptions | None = None
) -> SessionExec:
    """Build a ``session.exec`` with every field spelled."""
    asked = options or ExecOptions()
    extra = {} if asked.max_output_bytes is None else {"max_output_bytes": asked.max_output_bytes}
    return SessionExec(
        lease_id=lease_id,
        argv=tuple(argv),
        cwd=asked.cwd,
        env=tuple(EnvVar(name=name, value=value) for name, value in asked.env),
        timeout_s=asked.timeout_s,
        has_stdin=asked.has_stdin,
        task_id=None,
        worker_id=None,
        **extra,
    )


async def run_exec(
    queen: QueenDouble,
    session: QueenSession,
    lease_id: str,
    argv: Sequence[str],
    options: ExecOptions | None = None,
) -> ExecResult:
    """Run a program and wait for its ``session.exit``."""
    message = exec_message(lease_id, argv, options)
    sent = await session.send(message, sender=queen.warden_id)
    ended = await session.expect(SessionExit, where=lambda e: e.correlation_id == sent.id)
    outputs = tuple(
        e.payload
        for e in session.frames
        if isinstance(e.payload, SessionOutput) and e.correlation_id == sent.id
    )
    return ExecResult(sent, ended.payload, outputs)


async def put_file(
    queen: QueenDouble, session: QueenSession, lease_id: str, path: str, data: bytes
) -> Envelope:
    """Write a file in as few chunks as fit; return the frame that ended it (exit or error)."""
    return await put_chunked(queen, session, lease_id, Upload(path, data))


async def put_chunked(
    queen: QueenDouble, session: QueenSession, lease_id: str, upload: Upload
) -> Envelope:
    """Write a file as the Warden would, chunk by chunk, and return the frame that ended it."""
    transfer_id = new_message_id(queen.clock)
    data, size = upload.data, upload.chunk_size
    digest = hashlib.sha256(data).hexdigest()
    offsets = list(range(0, max(len(data), 1), size))
    for index, offset in enumerate(offsets):
        final = index == len(offsets) - 1
        chunk = SessionPutFile(
            lease_id=lease_id,
            transfer_id=transfer_id,
            total_bytes=len(data),
            path=upload.path,
            chunk=data[offset : offset + size],
            offset=offset,
            final=final,
            sha256=digest if final else None,
            is_executable=upload.is_executable,
            task_id=None,
            worker_id=None,
        )
        # The first chunk's envelope id is the transfer's identity, which every chunk echoes.
        named = Overrides(message_id=transfer_id) if index == 0 else None
        await session.send(chunk, sender=queen.warden_id, overrides=named)
    return await session.expect_envelope(
        SessionExit, ErrorMessage, where=lambda e: e.correlation_id == transfer_id
    )


async def get_file(
    queen: QueenDouble,
    session: QueenSession,
    lease_id: str,
    path: str,
    max_bytes: int | None = None,
) -> Envelope | FileResult:
    """Read a file; the result is a FileResult, or the control.error that refused it."""
    extra = {} if max_bytes is None else {"max_bytes": max_bytes}
    request = SessionGetFile(lease_id=lease_id, path=path, task_id=None, worker_id=None, **extra)
    sent = await session.send(request, sender=queen.warden_id)
    ended = await session.expect_envelope(
        SessionExit, ErrorMessage, where=lambda e: e.correlation_id == sent.id
    )
    if isinstance(ended.payload, ErrorMessage):
        return ended
    assert isinstance(ended.payload, SessionExit)
    chunks = sorted(_file_chunks(session, sent.id), key=lambda c: c.offset)
    content = b"".join(chunk.chunk for chunk in chunks)
    return FileResult(ended.payload, content, hashlib.sha256(content).hexdigest())


def _file_chunks(session: QueenSession, request_id: str) -> list[SessionOutput]:
    """Return the FILE chunks that answered ``request_id``, in arrival order."""
    return [
        e.payload
        for e in session.frames
        if isinstance(e.payload, SessionOutput)
        and e.correlation_id == request_id
        and e.payload.stream is SessionStream.FILE
    ]


async def teardown(
    queen: QueenDouble,
    session: QueenSession,
    lease_id: str,
    *,
    cause: ReleaseCause = ReleaseCause.COMPLETED,
    urgency: Urgency = Urgency.GRACEFUL,
) -> Envelope:
    """Ask for a release as the Warden; return the next frame answering it (release or error)."""
    request = CellTeardownRequest(
        cell_id=queen.cell_id,
        lease_id=lease_id,
        urgency=urgency,
        cause=cause,
        reason="A test is done with the device.",
    )
    sent = await session.send(request, sender=queen.warden_id)
    return await session.expect_envelope(where=lambda e: e.correlation_id == sent.id)
