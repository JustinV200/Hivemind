"""Run one program for a lease: spawn it in its own group, stream its output, watch its limits.

This is the machinery behind ``session.exec`` (gateway-contract section 11). A Warden (the
per-Cell supervisor) asks the Pollen Packet (the thin gateway program on a device the Hive
borrows) to run an argument list inside a lease (the Hive's temporary hold on the device). The
program starts as the leader of a process group of its own, and the group is recorded in the lease
table *before* the first output frame, so a crash leaks no process. Standard output and error are
read concurrently and sent as ``session.output`` chunks; one ``session.exit`` ends the run, queued
durably in the outbox so it reaches the Queen (the Hive's central orchestrator) even if the link
dropped meanwhile. A program that cannot start is not an error reply: it exits 127 after one
stderr line, as a shell would report it. The run belongs to its lease, not to a connection: when
the link drops the frames are discarded and the program keeps running. Cancelling the run (the
packet is stopping) kills the whole group first.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built and tracked by
    pollen.executors.session; sends through the Reporter, records through the lease table and
    signals through the platform shim; calls into waggle and the standard library only.

Key invariants:
    - The pid is recorded in the lease table before the first output frame is sent.
    - Exactly one ``session.exit`` ends a run, after both streams' final chunks.
    - A program the packet could not record in the lease table does not run.
    - A program runs in the packet's environment as ``child_environment`` leaves it, with the
      request's variables laid over it: a frozen packet's own library path is not inherited.
    - A cancelled run leaves no process behind.

See Also:
    - docs/waggle/gateway-contract.md section 11 (exec semantics).
    - packages/hivemind/src/hivemind/cell/local/process.py for the semantics this matches.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

from pollen.errors import LeaseNotOpenError, StateError
from pollen.executors.base import ExecutorDeps
from pollen.executors.run.control import RunControl, RunResult
from pollen.executors.run.plan import ExecPlan
from pollen.executors.run.streams import OutputSink, StdinFeeder
from pollen.lease.lease import ProcessKind, ProcessRecord
from pollen.platform.base import ProcessRef, SpawnSpec, child_environment, stop_group
from waggle.messages.session import (
    SessionExit,
    SessionOutcome,
    SessionRequestKind,
    SessionStdin,
    SessionStream,
)

EXIT_COMMAND_NOT_STARTED = 127  # What a shell reports for a command it cannot start.
DRAIN_GRACE_S = 1.0  # After the leader exits, how long to wait for the pipes to reach EOF.
STOP_WAIT_S = 10.0  # The longest a release waits for a killed run to finish reporting.

__all__ = ["EXIT_COMMAND_NOT_STARTED", "ExecRun"]


class ExecRun:
    """One running exec: spawn, stream, watch and report.

    Owns mutable state (codingrules 8.5, documented): the process reference and the three helpers
    that hold the run's counters, verdict and queue; only the run's own tasks and the reader's
    ``feed`` touch them.
    """

    def __init__(self, plan: ExecPlan, deps: ExecutorDeps) -> None:
        """Prepare a run; nothing is started until ``execute``.

        Args:
            plan: The resolved exec.
            deps: The shared collaborators (reporter, lease table, clock, platform shim).
        """
        self._plan = plan
        self._deps = deps
        self._sink = OutputSink(plan, deps.reporter)
        self._feeder = StdinFeeder()
        self._control = RunControl(plan, deps)
        self._ref: ProcessRef | None = None
        self.finished = asyncio.Event()  # Set when the run is over; its exit is then queued.

    async def execute(self) -> None:
        """Run to the end and queue the one ``session.exit``; the body of the run's task."""
        started = self._deps.clock.monotonic()
        try:
            outcome, code, name, reason = await self._run()
        finally:
            self.finished.set()
        exit_message = SessionExit(
            lease_id=self._plan.lease_id,
            request_id=self._plan.request_id,
            request_kind=SessionRequestKind.EXEC,
            outcome=outcome,
            exit_code=code,
            signal_name=name,
            duration_s=max(0.0, self._deps.clock.monotonic() - started),
            size_bytes=None,
            sha256=None,
            reason=reason,
        )
        await self._deps.reporter.send_durable(
            exit_message, to=self._plan.requester, correlation_id=self._plan.request_id
        )

    def feed(self, event: SessionStdin) -> None:
        """Hand a ``session.stdin`` event to the run without ever blocking the reader.

        Args:
            event: Stdin bytes (or a close), or a signal to deliver.
        """
        # A signal must never queue behind bytes a stuck child will not read, so it is delivered
        # here, at once; bytes and the close go through the queue to the writer task.
        if event.signal is not None:
            self._control.deliver(event.signal)
        else:
            self._feeder.put(event)

    async def stop(self, reason: str, *, graceful: bool) -> bool:
        """End the run from the gateway's side (a release, a session close) and wait for it.

        Args:
            reason: Why, for the exit's reason text.
            graceful: Ask the group to terminate and wait a short grace before forcing it.

        Returns:
            True if the program was still running when stopped.
        """
        self._control.set_verdict(SessionOutcome.KILLED, reason)
        alive = await self._control.end_group(graceful=graceful)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(STOP_WAIT_S):  # The run reports and finishes promptly.
                await self.finished.wait()
        return alive

    async def _run(self) -> RunResult:
        """Spawn, pump and watch; return the outcome, exit code, signal name and reason."""
        process = await self._spawn()
        if process is None:
            reason = "The program could not start."
            return SessionOutcome.EXITED, EXIT_COMMAND_NOT_STARTED, None, reason
        self._ref = self._deps.shim.process_ref(process.pid)
        if not await self._record(process, self._ref):
            await self._sink.end_all()
            return SessionOutcome.KILLED, None, None, "The program could not be recorded."
        self._control.attach(self._ref)
        if self._control.has_verdict:
            await self._control.end_group(graceful=False)  # Released before it got going.
        try:
            returncode = await self._supervise(process, self._deps.clock.monotonic())
        except asyncio.CancelledError:
            self._control.kill_now()  # The packet is stopping: nothing may outlive it.
            raise
        await self._prune(self._ref)
        return self._control.result(returncode)

    async def _supervise(self, process: asyncio.subprocess.Process, started: float) -> int:
        """Pump both pipes, watch the limits and feed stdin until the leader exits."""
        control, sink = self._control, self._sink
        async with asyncio.TaskGroup() as group:
            out = group.create_task(
                sink.pump(process.stdout, SessionStream.STDOUT, control.on_output_cap)
            )
            err = group.create_task(
                sink.pump(process.stderr, SessionStream.STDERR, control.on_output_cap)
            )
            watchdog = group.create_task(control.watch(started))
            writer = group.create_task(self._feeder.run(process))
            returncode = await process.wait()  # A local child: ends when it or a kill does.
            writer.cancel()
            if not control.is_killing:
                watchdog.cancel()  # Nothing is left to watch once the leader is gone.
            await asyncio.wait({watchdog})  # A kill already under way finishes first.
            await asyncio.wait((out, err), timeout=DRAIN_GRACE_S)
            out.cancel()  # A surviving child may still hold a pipe open; it stays with the lease.
            err.cancel()
        await sink.end_all()
        return returncode

    async def _spawn(self) -> asyncio.subprocess.Process | None:
        """Start the program; on failure send the one stderr line and return None."""
        plan = self._plan
        spec = SpawnSpec(
            argv=plan.argv,
            cwd=plan.cwd,
            # The device's environment with the request's overlaid, not the packet's own: a
            # frozen packet's unpacked library path must not reach a program that has libraries
            # of its own (gateway-contract section 11).
            env={**child_environment(os.environ), **plan.env},
            stdin=asyncio.subprocess.PIPE if plan.has_stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            return await self._deps.shim.spawn(spec)
        except (OSError, ValueError) as error:
            # Reported the way a shell reports a command it cannot find: an ordinary failed
            # command on stderr, not a protocol error (gateway-contract section 11).
            line = f"{plan.argv[0]}: {getattr(error, 'strerror', None) or error}\n"
            await self._sink.send(SessionStream.STDERR, line.encode("utf-8", "replace"))
            await self._sink.end_all()
            return None

    async def _record(self, process: asyncio.subprocess.Process, ref: ProcessRef) -> bool:
        """Persist the pid with the lease before any output; False (and kill) if that fails."""
        record = ProcessRecord(pid=ref.pid, created=ref.created, kind=ProcessKind.EXEC)
        try:
            await self._deps.leases.modify(
                self._plan.lease_id, lambda stored: stored.with_process(record)
            )
        except (LeaseNotOpenError, StateError):
            # Released meanwhile, or the table cannot be written: an unrecorded process would
            # be one the lease can no longer undo, so it does not run.
            await stop_group(self._deps.shim, ref, graceful=False, clock=self._deps.clock)
            await process.wait()
            return False
        return True

    async def _prune(self, ref: ProcessRef) -> None:
        """Forget the pid once its whole group is gone; a surviving child keeps it recorded."""
        if self._deps.shim.group_alive(ref):
            return
        with contextlib.suppress(LeaseNotOpenError):
            await self._deps.leases.modify(
                self._plan.lease_id, lambda stored: stored.without_process(ref.pid)
            )
