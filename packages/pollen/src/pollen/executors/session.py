"""Serve the session's terminal requests: open, exec and stdin, one exec per run.

A session is how a Warden (the per-Cell supervisor) drives a Real Cell (an existing device the
Hive borrows for a task) through its Pollen Packet (the thin gateway program on the device)
exactly as it would a local terminal. It is identified by its lease (the Hive's temporary hold on
the device), one session per lease, and concurrency lives at the exec level: each exec is keyed by
its request's envelope id, several may run at once, and stdin and signals find a run by that id.
``prepare`` finds the lease, resolves the working directory, names what the exec needs (the
program, and the directory if it is outside scratch) and returns; the work, once the dispatcher
has checked the grant, is a run spawned in the packet's task scope, so a long exec never holds up
the reader, the heartbeat or the next request. A run belongs to its lease: this executor can stop
every run of a lease on release or on a closed session, each reporting KILLED.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root
    and consulted by pollen.agent.dispatch; ends its runs when pollen.agent.leases calls
    ``stop_lease``; calls into pollen.executors.run, the lease table and waggle only.

Key invariants:
    - Every session request ends in exactly one ``session.exit`` or one ``control.error``.
    - A run is tracked from the moment it is accepted until its exit is queued, and no longer.
    - Session traffic for a lease that is not open, or whose session was closed, is refused with
      ``hive.session.lease_not_open``.
    - Stdin for an exec that has ended, or one that belongs to another lease, is dropped.

See Also:
    - docs/waggle/gateway-contract.md sections 9.2 and 11.
    - pollen.executors.run for the run itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from pollen.errors import LeaseNotOpenError
from pollen.executors.base import (
    EXEC,
    OUTSIDE_SCRATCH,
    ExecutorDeps,
    Need,
    Prepared,
    payload_of,
)
from pollen.executors.paths import is_within, resolve_path
from pollen.executors.run import ExecPlan, ExecRun
from pollen.lease.lease import LeaseState
from waggle.envelope import Envelope
from waggle.ids import LeaseId, MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.session import (
    SessionExec,
    SessionExit,
    SessionOpen,
    SessionOutcome,
    SessionRequestKind,
    SessionStdin,
)

log = logging.getLogger(__name__)

__all__ = ["SessionExecutor"]


class SessionExecutor:
    """Opens sessions, starts execs, feeds their stdin and ends them with the lease.

    Owns mutable state (codingrules 8.5, documented): ``_runs`` maps an exec's request id to its
    lease and running ``ExecRun`` from acceptance until its exit has been queued.
    """

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (SessionOpen, SessionExec, SessionStdin)

    def __init__(self, deps: ExecutorDeps) -> None:
        """Bind the executor to the shared collaborators.

        Args:
            deps: State, leases, reporter, clock, platform shim, layout and task scope.
        """
        self._deps = deps
        self._runs: dict[MessageId, tuple[LeaseId, ExecRun]] = {}  # Request id -> lease, run.
        self._preparers: dict[type[WaggleMessage], Callable[[Envelope], Prepared]] = {
            SessionOpen: self._prepare_open,
            SessionExec: self._prepare_exec,
            SessionStdin: self._prepare_stdin,
        }

    def prepare(self, envelope: Envelope) -> Prepared:
        """Read a session request without changing anything.

        Args:
            envelope: A ``session.open``, ``session.exec`` or ``session.stdin``.

        Returns:
            What it needs and how to run it.

        Raises:
            LeaseNotOpenError: The lease is unknown, releasing or its session was closed.
            PathRefusedError: The working directory cannot be resolved safely.
        """
        return self._preparers[type(envelope.payload)](envelope)

    async def stop_lease(self, lease_id: LeaseId, *, graceful: bool, reason: str) -> int:
        """End every exec of ``lease_id``; each reports KILLED with ``reason``.

        Args:
            lease_id: The lease being closed or released.
            graceful: Ask each group to terminate and wait a short grace before forcing it.
            reason: Why, for the exits.

        Returns:
            How many programs were still running when stopped.
        """
        runs = [run for owner, run in self._runs.values() if owner == lease_id]
        stopped = await asyncio.gather(*(run.stop(reason, graceful=graceful) for run in runs))
        return sum(1 for was_alive in stopped if was_alive)

    # ──────────────────────────────────────────────────────────────────────────
    # session.open
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_open(self, envelope: Envelope) -> Prepared:
        """Check the lease is open and, when run, answer COMPLETED (reopening a closed session)."""
        payload = payload_of(envelope, SessionOpen)
        lease = self._deps.leases.get(payload.lease_id)
        if lease is None or lease.state is not LeaseState.OPEN:
            raise LeaseNotOpenError(f"Lease {payload.lease_id} is not open on this device.")

        async def run() -> None:
            if lease.is_session_closed:
                await self._deps.leases.modify(
                    payload.lease_id,
                    lambda stored: stored.model_copy(update={"is_session_closed": False}),
                )
            await self._answer_open(envelope, payload.lease_id)

        return Prepared(run=run)

    async def _answer_open(self, request: Envelope, lease_id: LeaseId) -> None:
        """Send the ``session.exit`` that completes an open."""
        exit_message = SessionExit(
            lease_id=lease_id,
            request_id=request.id,
            request_kind=SessionRequestKind.OPEN,
            outcome=SessionOutcome.COMPLETED,
            exit_code=None,
            signal_name=None,
            duration_s=0.0,
            size_bytes=None,
            sha256=None,
            reason="The session is open.",
        )
        await self._deps.reporter.send(exit_message, to=request.sender, correlation_id=request.id)

    # ──────────────────────────────────────────────────────────────────────────
    # session.exec and session.stdin
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_exec(self, envelope: Envelope) -> Prepared:
        """Resolve the working directory and name what the exec needs."""
        payload = payload_of(envelope, SessionExec)
        lease = self._deps.leases.require_session(payload.lease_id)
        scratch = Path(lease.scratch_root)
        needs = [Need(EXEC, payload.argv[0])]  # The program exactly as given, not a PATH lookup.
        cwd = scratch
        if payload.cwd is not None:
            cwd = resolve_path(scratch, payload.cwd, payload.lease_id)
            if not is_within(scratch, cwd):
                needs.append(Need(OUTSIDE_SCRATCH, str(cwd)))
        plan = ExecPlan(
            request_id=envelope.id,
            lease_id=payload.lease_id,
            requester=envelope.sender,
            argv=payload.argv,
            cwd=cwd,
            env={variable.name: variable.value for variable in payload.env},
            timeout_s=payload.timeout_s,
            has_stdin=payload.has_stdin,
            max_output_bytes=payload.max_output_bytes,
            scratch_root=scratch,
            quota_bytes=lease.quota_bytes,
        )

        async def run() -> None:
            self._start(plan)

        return Prepared(run=run, needs=tuple(needs), scratch_root=lease.scratch_root)

    def _prepare_stdin(self, envelope: Envelope) -> Prepared:
        """Find the exec a stdin event targets; input for one that ended is dropped."""
        payload = payload_of(envelope, SessionStdin)
        self._deps.leases.require_session(payload.lease_id)

        async def run() -> None:
            owner, exec_run = self._runs.get(payload.exec_id, (None, None))
            if exec_run is not None and owner == payload.lease_id:
                exec_run.feed(payload)

        return Prepared(run=run)

    def _start(self, plan: ExecPlan) -> None:
        """Register the run and hand it to the task scope; the reader returns at once."""
        run = ExecRun(plan, self._deps)
        # Registered before the task starts, so a stdin frame right behind the exec finds it.
        self._runs[plan.request_id] = (plan.lease_id, run)
        self._deps.scope.spawn(self._supervise(run, plan), name="exec")

    async def _supervise(self, run: ExecRun, plan: ExecPlan) -> None:
        """Run an exec to its exit; a failure of the packet's own is reported, not propagated."""
        try:
            await run.execute()
        except Exception as error:  # SAFETY: top of a spawned task; one bug must not end the group.
            # By type only: the text of an error can carry a path or an argument.
            log.error("pollen.exec_failed error=%s", type(error).__name__)
            await self._report_failure(plan)
        finally:
            self._runs.pop(plan.request_id, None)

    async def _report_failure(self, plan: ExecPlan) -> None:
        """Send the exit an exec that failed inside the packet never got to send."""
        exit_message = SessionExit(
            lease_id=plan.lease_id,
            request_id=plan.request_id,
            request_kind=SessionRequestKind.EXEC,
            outcome=SessionOutcome.KILLED,
            exit_code=None,
            signal_name=None,
            duration_s=0.0,
            size_bytes=None,
            sha256=None,
            reason="The packet failed while running it.",
        )
        await self._deps.reporter.send_durable(
            exit_message, to=plan.requester, correlation_id=plan.request_id
        )
