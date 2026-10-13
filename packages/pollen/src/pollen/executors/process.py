"""Serve background processes: start one, stop one, ask about one, all inside a lease.

An exec runs a program to its end; a background process must outlive the request that started it
(a virtual display, a sound server, a browser the Exoskeleton needs for the whole task). The
Pollen Packet (the thin gateway program on a device the Hive borrows) starts it in a process group
of its own with standard input closed and its output going to a log file or nowhere, and records
its pid in the lease table *before it answers*, so a crash right after the start cannot leak it.
A lease (the Hive's temporary hold on the device) kills every background process on release, and
the Warden (the per-Cell supervisor) can stop or query one by pid, but only a pid *this lease*
started: any other draws ``pollen.not_found``, so one lease can never signal another's process or
the device's own (gateway-contract section 9.3).

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root
    and consulted by pollen.agent.dispatch; ends its processes when pollen.agent.leases calls
    ``stop_lease``; calls into pollen.lease, the platform shim and waggle only.

Key invariants:
    - The pid is in the lease table before ``session.process_state`` is sent for a START.
    - A pid the lease did not start is never signalled, queried or reported.
    - A log file outside scratch is recorded before it is created, like any outside write.
    - Every process this executor started is either tracked here or reaped.

See Also:
    - docs/waggle/gateway-contract.md section 9.3 (background processes).
    - packages/hivemind/src/hivemind/cell/local/background.py for the semantics this matches.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import IO, ClassVar

from pollen.errors import LeaseNotOpenError, NotFoundError, StartFailedError, StateError
from pollen.executors.base import (
    EXEC,
    OUTSIDE_SCRATCH,
    ExecutorDeps,
    Need,
    Prepared,
    payload_of,
)
from pollen.executors.paths import is_within, resolve_path, write_needs
from pollen.lease.lease import DeviceLease, ProcessKind, ProcessRecord
from pollen.lease.restore import record_before_change
from pollen.platform.base import ProcessRef, SpawnSpec, stop_group
from waggle.envelope import Envelope
from waggle.ids import LeaseId
from waggle.messages.base import WaggleMessage
from waggle.messages.session import ProcessAction, SessionProcess, SessionProcessState

MAX_TRACKED_PROCESSES = 256  # Processes one lease may track; more means something is looping.
REAP_WAIT_S = 5.0  # How long a stopped process may take to be reaped before it is left.

__all__ = ["ProcessExecutor"]


class ProcessExecutor:
    """Starts, stops and queries a lease's background processes.

    Owns mutable state (codingrules 8.5, documented): ``_handles`` keeps the asyncio handle of
    every process this executor started, by lease and pid, until it is stopped and reaped.
    """

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (SessionProcess,)

    def __init__(self, deps: ExecutorDeps) -> None:
        """Bind the executor to the shared collaborators.

        Args:
            deps: State, leases, reporter, clock, platform shim, layout and task scope.
        """
        self._deps = deps
        self._handles: dict[tuple[LeaseId, int], asyncio.subprocess.Process] = {}

    def prepare(self, envelope: Envelope) -> Prepared:
        """Resolve a process request and name what it needs, changing nothing.

        Args:
            envelope: A ``session.process``.

        Returns:
            What it needs and how to run it.

        Raises:
            LeaseNotOpenError: The lease is unknown, releasing or its session was closed.
            NotFoundError: A STOP or QUERY names a pid this lease did not start.
            PathRefusedError: The working directory or log path cannot be resolved safely.
        """
        payload = payload_of(envelope, SessionProcess)
        lease = self._deps.leases.require_session(payload.lease_id)
        if payload.action is ProcessAction.START:
            return self._prepare_start(envelope, payload, lease)
        record = next((p for p in lease.processes if p.pid == payload.pid), None)
        if record is None:
            raise NotFoundError(f"Lease {payload.lease_id} did not start the process asked about.")
        handler = self._stop if payload.action is ProcessAction.STOP else self._query
        return Prepared(run=lambda: handler(envelope, payload, record))

    async def stop_lease(self, lease_id: LeaseId, *, graceful: bool, reason: str) -> int:
        """Stop every background process of ``lease_id`` and reap it.

        Args:
            lease_id: The lease being closed or released.
            graceful: Ask each group to terminate and wait a short grace before forcing it.
            reason: Unused; a background process has no exit to explain.

        Returns:
            How many processes were still running when stopped.
        """
        keys = [key for key in self._handles if key[0] == lease_id]
        stopped = 0
        for key in keys:
            process = self._handles.pop(key)
            ref = self._deps.shim.process_ref(process.pid)
            if await stop_group(self._deps.shim, ref, graceful=graceful, clock=self._deps.clock):
                stopped += 1
            await _reap(process)
        return stopped

    # ──────────────────────────────────────────────────────────────────────────
    # START
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_start(
        self, envelope: Envelope, payload: SessionProcess, lease: DeviceLease
    ) -> Prepared:
        """A START needs the program, plus the rules for its directory and its log file."""
        scratch = Path(lease.scratch_root)
        needs = [Need(EXEC, payload.argv[0])]
        cwd = scratch
        if payload.cwd is not None:
            cwd = resolve_path(scratch, payload.cwd, payload.lease_id)
            if not is_within(scratch, cwd):
                needs.append(Need(OUTSIDE_SCRATCH, str(cwd)))
        log_path = None
        if payload.log_path is not None:
            log_path = resolve_path(scratch, payload.log_path, payload.lease_id)
            needs.extend(write_needs(log_path, scratch))

        async def run() -> None:
            await self._start(envelope, payload, lease, (cwd, log_path))

        return Prepared(run=run, needs=tuple(needs), scratch_root=lease.scratch_root)

    async def _start(
        self,
        envelope: Envelope,
        payload: SessionProcess,
        lease: DeviceLease,
        where: tuple[Path, Path | None],
    ) -> None:
        """Spawn the process, record its pid with the lease, then answer."""
        cwd, log_path = where
        if len(lease.processes) >= MAX_TRACKED_PROCESSES:
            raise StartFailedError(f"Lease {lease.lease_id} already tracks too many processes.")
        log_file = await _open_log(self._deps, lease, log_path)
        try:
            process = await self._deps.shim.spawn(
                SpawnSpec(
                    argv=payload.argv,
                    cwd=cwd,
                    env={**os.environ, **{v.name: v.value for v in payload.env}},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log_file if log_file is not None else asyncio.subprocess.DEVNULL,
                    stderr=log_file if log_file is not None else asyncio.subprocess.DEVNULL,
                )
            )
        except (OSError, ValueError) as error:
            raise StartFailedError(
                f"The program of request {envelope.id} could not be started."
            ) from error
        finally:
            if log_file is not None:
                log_file.close()  # The child holds its own copy of the descriptor.
        ref = self._deps.shim.process_ref(process.pid)
        await _record(self._deps, lease, process, ref)
        self._handles[(lease.lease_id, process.pid)] = process
        state = SessionProcessState(
            lease_id=lease.lease_id,
            action=ProcessAction.START,
            pid=process.pid,
            argv=payload.argv,
            log_path=None if log_path is None else str(log_path),
            is_running=process.returncode is None,
            was_running=None,
            reason="The process was started.",
        )
        await self._deps.reporter.send(state, to=envelope.sender, correlation_id=envelope.id)

    # ──────────────────────────────────────────────────────────────────────────
    # STOP and QUERY
    # ──────────────────────────────────────────────────────────────────────────

    async def _stop(
        self, envelope: Envelope, payload: SessionProcess, record: ProcessRecord
    ) -> None:
        """Terminate the process group politely, then by force, and report what it was doing."""
        ref = ProcessRef(record.pid, record.created)
        was_running = self._is_running(payload.lease_id, ref)
        await stop_group(self._deps.shim, ref, graceful=True, clock=self._deps.clock)
        handle = self._handles.pop((payload.lease_id, record.pid), None)
        if handle is not None:
            await _reap(handle)
        await self._reply(envelope, payload, record, (False, was_running, "It was stopped."))

    async def _query(
        self, envelope: Envelope, payload: SessionProcess, record: ProcessRecord
    ) -> None:
        """Report whether the process is still running."""
        ref = ProcessRef(record.pid, record.created)
        running = self._is_running(payload.lease_id, ref)
        await self._reply(envelope, payload, record, (running, None, "It was asked about."))

    async def _reply(
        self,
        envelope: Envelope,
        payload: SessionProcess,
        record: ProcessRecord,
        outcome: tuple[bool, bool | None, str],
    ) -> None:
        """Send the ``session.process_state`` for a STOP or QUERY."""
        is_running, was_running, reason = outcome
        state = SessionProcessState(
            lease_id=payload.lease_id,
            action=payload.action,
            pid=record.pid,
            argv=(),
            log_path=None,
            is_running=is_running,
            was_running=was_running,
            reason=reason,
        )
        await self._deps.reporter.send(state, to=envelope.sender, correlation_id=envelope.id)

    def _is_running(self, lease_id: LeaseId, ref: ProcessRef) -> bool:
        """Return whether the leader (when this executor holds it) or the group still runs."""
        handle = self._handles.get((lease_id, ref.pid))
        if handle is not None:
            return handle.returncode is None
        return self._deps.shim.group_alive(ref)


def _open_for_append(path: Path) -> IO[bytes]:
    """Create ``path``'s directories and open it for appending bytes (blocking)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("ab")


async def _open_log(
    deps: ExecutorDeps, lease: DeviceLease, log_path: Path | None
) -> IO[bytes] | None:
    """Open the log for appending, recording an outside-scratch log first."""
    if log_path is None:
        return None
    if not is_within(Path(lease.scratch_root), log_path):
        await record_before_change(deps.leases, deps.layout, lease, log_path)
    try:
        return await asyncio.to_thread(_open_for_append, log_path)
    except OSError as error:
        raise StartFailedError(
            f"The log file for lease {lease.lease_id} could not be opened."
        ) from error


async def _record(
    deps: ExecutorDeps, lease: DeviceLease, process: asyncio.subprocess.Process, ref: ProcessRef
) -> None:
    """Persist the pid before answering; kill the process if the lease cannot hold it."""
    record = ProcessRecord(pid=ref.pid, created=ref.created, kind=ProcessKind.BACKGROUND)
    try:
        await deps.leases.modify(lease.lease_id, lambda stored: stored.with_process(record))
    except (LeaseNotOpenError, StateError) as error:
        await stop_group(deps.shim, ref, graceful=False, clock=deps.clock)
        await _reap(process)
        raise StartFailedError(
            f"The process could not be recorded with lease {lease.lease_id}."
        ) from error


async def _reap(process: asyncio.subprocess.Process) -> None:
    """Wait for a killed child to be reaped, bounded; a stuck one is left to the OS."""
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(REAP_WAIT_S):
            await process.wait()
