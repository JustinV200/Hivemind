"""Define DeviceExecutor, the seam between a checked request and the work it asks of the device.

A Warden (the per-Cell supervisor) or the Queen (the Hive's central orchestrator) asks the Pollen
Packet (the thin gateway program on a device the Hive borrows) to run a program, move a file,
start a background process or look at the device. Every one of those is handled by a
``DeviceExecutor`` in two steps, so the packet can check the request *before* anything runs
(ADR-0051): ``prepare`` reads the request, finds its lease (the Hive's temporary hold on the
device), resolves every path and returns what the request ``Need``s (a capability family and a
scope, such as ``exec`` and the program name) together with the coroutine that will do the work;
the dispatcher checks the needs against the node's granted capabilities and only then awaits it.
The work is quick to start: anything long (an exec, a file read streaming back) is spawned as a
task owned by the lease, never by the connection, because a dropped link stops the streaming and
not the process. This module also holds the small Protocols the executors share: the ``Reporter``
they send frames through and the ``LeaseStopper`` the release calls to end what a lease started.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Implemented by the session,
    file, process and observe executors; used by pollen.agent.dispatch (check then run) and
    pollen.agent.leases (stop a lease's work); calls into waggle and pollen.state, the lease
    table and the platform shim only.

Key invariants:
    - ``prepare`` has no side effect: it may refuse a request but never changes the device.
    - The capability families are named once, here; the matcher in pollen.agent.checks and every
      executor spell them the same way.
    - Executors send through ``Reporter`` and never hold a connection.

See Also:
    - docs/waggle/gateway-contract.md sections 9 to 11 for what each request asks of the device.
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the local check.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from types import TracebackType
from typing import ClassVar, Protocol

from pollen.errors import InvalidRequestError, PollenError
from pollen.lease.table import LeaseTable
from pollen.platform.base import PlatformShim
from pollen.state import DataLayout, StateStore
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import LeaseId, MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS
from waggle.messages.session import SessionPutFile

log = logging.getLogger(__name__)

# The five capability families a packet understands (gateway-contract section 10).
EXEC = "exec"  # Run a program; scope is the program as given in argv[0].
FS_READ = "fs:read"  # Read a path; scope is the resolved path.
FS_WRITE = "fs:write"  # Write or delete a path; scope is the resolved path.
OUTSIDE_SCRATCH = "cell:outside_scratch"  # Touch a path outside scratch; scope is that path.
WATCH = "watch"  # Observe the Cell read-only; scope is the cell id.

__all__ = [
    "EXEC",
    "FS_READ",
    "FS_WRITE",
    "OUTSIDE_SCRATCH",
    "WATCH",
    "DeviceExecutor",
    "ExecutorDeps",
    "LeaseStopper",
    "Need",
    "Prepared",
    "Reporter",
    "TaskScope",
    "error_payload",
    "payload_of",
    "request_id_of",
]


@dataclass(frozen=True, slots=True)
class Need:
    """One capability a request needs: a family and the scope it is asked for."""

    family: str  # One of EXEC, FS_READ, FS_WRITE, OUTSIDE_SCRATCH or WATCH.
    scope: str  # The program, the resolved path or the cell id the request names.


@dataclass(frozen=True, slots=True)
class Prepared:
    """A request that was read and resolved but not yet run."""

    run: Callable[[], Awaitable[None]]  # Does the work; awaited only after the needs are met.
    needs: tuple[Need, ...] = ()  # What the node must have been granted for it.
    scratch_root: str | None = None  # The lease's scratch, for the {scratch} grant substitution.


class Reporter(Protocol):
    """How an executor sends frames: live, or durably through the outbox."""

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: MessageId | None = None
    ) -> bool:
        """Send now if a connection is up; never queue.

        Args:
            payload: The message to send.
            to: The recipient's bee address.
            correlation_id: The request the message answers, if any.

        Returns:
            True if the frame was handed to the link; False if there is no connection.
        """
        ...

    async def send_durable(
        self, payload: WaggleMessage, *, to: str, correlation_id: MessageId | None = None
    ) -> None:
        """Queue in the outbox first, then send if a connection is up.

        Args:
            payload: The message that must reach the Queen eventually.
            to: The recipient's bee address.
            correlation_id: The request the message answers, if any.
        """
        ...


class LeaseStopper(Protocol):
    """Something that tracks work a lease started and can end it on release or session close."""

    async def stop_lease(self, lease_id: LeaseId, *, graceful: bool, reason: str) -> int:
        """End everything the lease started that this stopper tracks.

        Args:
            lease_id: The lease being closed or released.
            graceful: Ask processes to terminate and wait a short grace before forcing them.
            reason: Why, for the exit a stopped exec reports.

        Returns:
            How many processes were still running when stopped.
        """
        ...


class DeviceExecutor(Protocol):
    """One family of requests the packet can carry out on the device."""

    handles: ClassVar[tuple[type[WaggleMessage], ...]]  # The payload types it serves.

    def prepare(self, envelope: Envelope) -> Prepared:
        """Read and resolve a request without changing anything.

        Args:
            envelope: A verified, admitted request or event of one of ``handles``.

        Returns:
            What it needs and how to run it.

        Raises:
            PollenError: The request must be refused (an unknown lease, a bad path, ...).
        """
        ...


class TaskScope:
    """Owns the long-running tasks of the packet: execs, transfers, releases.

    A run belongs to its lease, not to the connection that asked for it, so these tasks live
    for the life of the packet. The scope is entered once by the packet's ``run`` (it wraps an
    ``asyncio.TaskGroup``), hands out tasks through ``spawn`` and cancels whatever is left when it
    exits, so every task has an owner that awaits it (codingrules section 11). A task that fails
    is logged by its type and ends alone; one bug never takes the packet's other work with it.

    Owns mutable state (codingrules 8.5, documented): the group and the set of live tasks.
    """

    def __init__(self) -> None:
        """Create a scope that has not been entered yet."""
        self._group: asyncio.TaskGroup | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> TaskScope:
        """Open the scope's task group."""
        self._group = asyncio.TaskGroup()
        await self._group.__aenter__()
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Cancel what is still running, wait for it, and close the group."""
        group, self._group = self._group, None
        for task in tuple(self._tasks):
            task.cancel()
        if group is not None:
            # The body's own exception is not handed to the group: it would come back wrapped in
            # an ExceptionGroup, and the caller wants it as it was raised. What the group does
            # raise is a task that failed while cancelled, which is a bug worth seeing.
            await group.__aexit__(None, None, None)

    def spawn(self, work: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
        """Start ``work`` as a task of this scope.

        Args:
            work: The coroutine; a failure in it is logged and ends only this task.
            name: A short label for debugging; never a path, argument or content.

        Returns:
            The task, owned by the scope.

        Raises:
            RuntimeError: The scope has not been entered, or has been left.
        """
        if self._group is None:
            work.close()  # Never started, so it must not warn that it was never awaited.
            raise RuntimeError("The task scope is not open; spawn inside 'async with scope'.")
        task = self._group.create_task(self._guarded(work, name), name=name)
        self._tasks.add(task)
        # A task cancelled before its first step never awaits ``work``; closing it here keeps the
        # unstarted coroutine from warning that it was never awaited (a no-op for a finished one).
        task.add_done_callback(lambda _done: work.close())
        task.add_done_callback(self._tasks.discard)
        return task

    @staticmethod
    async def _guarded(work: Coroutine[None, None, None], name: str) -> None:
        """Run ``work`` so that a bug in it is logged, not allowed to end the whole group."""
        try:
            await work
        except Exception as error:  # SAFETY: the top of a packet task; one failure ends only it.
            log.error("pollen.task_failed task=%s error=%s", name, type(error).__name__)


def payload_of[PayloadT: WaggleMessage](envelope: Envelope, expected: type[PayloadT]) -> PayloadT:
    """Return the envelope's payload as ``expected``, or refuse the frame.

    Args:
        envelope: The request.
        expected: The payload class the handler serves.

    Returns:
        The payload, typed.

    Raises:
        InvalidRequestError: The payload is of another class (a routing bug, never a peer's).
    """
    payload = envelope.payload
    if not isinstance(payload, expected):
        raise InvalidRequestError(f"Request {envelope.id} reached a handler for another kind.")
    return payload


def request_id_of(envelope: Envelope) -> MessageId:
    """Return the id an answer to ``envelope`` correlates to.

    Args:
        envelope: A request or an event that can fail.

    Returns:
        The envelope's own id, except for a put-file chunk, whose identity is its transfer: the
        first chunk's id, which every chunk echoes, so a failure at any chunk answers the
        transfer the Warden is waiting on.
    """
    payload = envelope.payload
    return payload.transfer_id if isinstance(payload, SessionPutFile) else envelope.id


def error_payload(error: PollenError, failed_kind: str | None) -> ErrorMessage:
    """Build the ``control.error`` payload that reports ``error`` to the sender.

    Args:
        error: A refusal; its code and retryability are fixed by its class.
        failed_kind: The kind of the request that failed, when known.

    Returns:
        The wire message; its text is the error's sentence, which names ids and never a path.
    """
    text = str(error) or error.code
    return ErrorMessage(
        code=error.code,
        message=text[:MAX_ERROR_MESSAGE_CHARS],
        failed_kind=failed_kind,
        is_retryable=error.is_retryable,
    )


@dataclass(frozen=True, slots=True)
class ExecutorDeps:
    """The collaborators the executors share, bundled so none takes more than a few."""

    state: StateStore  # The node's grants, read afresh for every request.
    leases: LeaseTable  # The persisted leases.
    reporter: Reporter  # How frames leave the packet.
    clock: Clock  # Time for durations, deadlines and grace periods.
    shim: PlatformShim  # Process groups and platform facts.
    layout: DataLayout  # Where scratch, backups and the key live.
    scope: TaskScope  # Owns the tasks that outlive a request.
