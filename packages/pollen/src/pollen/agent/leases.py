"""Open, close and release device leases, and sweep the ones a restart left behind.

A lease is the Hive's temporary hold on the device, and the Pollen Packet (the thin gateway program
on a device the Hive borrows) is the only one who can open or end it there (gateway-contract
section 9.1). Opening checks that the request is for this Cell and for the Cell's Warden (the
per-Cell supervisor), grants the lower of the access level asked for and the node's own, creates a
scratch directory for the lease alone, writes the lease into the persisted table *before* answering
and answers with ``cell.lease_opened``. Releasing is the reverse and is never skipped half way: it
ends every program the lease started (politely, then by force, or by force at once), puts back
every change the lease recorded outside scratch, removes the scratch directory, and queues a
``cell.lease_released`` in the outbox that says whether the device is exactly as it was found.
A release that cannot finish every step leaves the lease in the table in state RELEASING and is
tried again; one that finishes removes the lease. The same release runs for a teardown request, for
the dead-man switch (the Queen, the Hive's central orchestrator, has been silent too long), for the
sweep at startup that finds leases a crash left behind, and for a ``session.close``, which ends the
session's programs but leaves the lease open.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. A DeviceExecutor consulted by
    pollen.agent.dispatch and driven by pollen.agent.heartbeat (dead-man, retries) and
    pollen.agent.packet (startup sweep); stops work through the executors' ``LeaseStopper`` and
    undoes changes through pollen.lease; calls into the platform shim and waggle.

Key invariants:
    - The lease is in the table before ``cell.lease_opened`` is sent, and leaves it only after the
      ``cell.lease_released`` that reports it is in the outbox.
    - A lease is released by one task at a time; a second request for it is refused as not open.
    - ``is_restored`` is true only when every recorded path is back and scratch is gone, and
      ``residual_paths`` lists the rest.
    - A release that fails part way keeps the lease RELEASING; a retry says nothing until it
      succeeds.

See Also:
    - docs/waggle/gateway-contract.md sections 9.1, 12 and 13.
    - pollen.lease for the table, the restore records and the lease state machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import ClassVar

from pollen.agent.checks import parse_capability
from pollen.errors import LeaseNotOpenError, LeaseRefusedError, PollenError
from pollen.executors.base import (
    OUTSIDE_SCRATCH,
    ExecutorDeps,
    LeaseStopper,
    Prepared,
    payload_of,
)
from pollen.lease import (
    DEFAULT_SCRATCH_QUOTA_BYTES,
    DeviceLease,
    LeaseState,
    remove_scratch,
    replay_restores,
)
from pollen.platform.base import ProcessRef, stop_group
from pollen.state import PRIVATE_DIR_MODE
from waggle.envelope import Envelope
from waggle.ids import LeaseId, new_lease_id
from waggle.messages.base import MAX_PATH_CHARS, WaggleMessage
from waggle.messages.cell import (
    CellRequest,
    CellTeardownRequest,
    LeaseOpened,
    LeaseReleased,
    ReleaseCause,
)
from waggle.messages.cell.leases import MAX_ALLOWED_PATHS, MAX_RESIDUAL_PATHS
from waggle.messages.labels import AccessLevel, Urgency
from waggle.messages.session import SessionClose
from waggle.messages.swarm.enrolment import MAX_HEARTBEAT_LEASES

log = logging.getLogger(__name__)

MAX_OPEN_LEASES = MAX_HEARTBEAT_LEASES  # The heartbeat can name this many, so no more are opened.

__all__ = ["MAX_OPEN_LEASES", "LeaseService", "Release", "Releaser"]


@dataclass(frozen=True, slots=True)
class Release:
    """One release to carry out."""

    lease_id: LeaseId  # The lease to release.
    cause: ReleaseCause  # Why: a decided cause, DEAD_MAN or ORPHAN_SWEEP.
    graceful: bool  # Ask programs to terminate first, or force them at once.
    reply_to: Envelope | None = None  # The teardown request being answered, if any.
    is_retry: bool = False  # A retry says nothing unless it finally succeeds.


class Releaser:
    """Ends leases: stops their work, undoes their changes and reports how it went.

    Owns mutable state (codingrules 8.5, documented): ``busy``, the ids of leases whose release is
    running, so that one lease is never released by two tasks at once.
    """

    def __init__(self, deps: ExecutorDeps, stoppers: Sequence[LeaseStopper]) -> None:
        """Bind the releaser to the shared collaborators and to whatever tracks a lease's work.

        Args:
            deps: State, lease table, reporter, clock, platform shim and layout.
            stoppers: The executors that can end the work a lease started.
        """
        self._deps = deps
        self._stoppers = tuple(stoppers)
        self.busy: set[LeaseId] = set()

    async def release_all(self, cause: ReleaseCause, *, include_releasing: bool) -> None:
        """Release every lease at once (the dead-man switch, the startup sweep).

        Args:
            cause: DEAD_MAN or ORPHAN_SWEEP.
            include_releasing: Also take the leases a failed release left in RELEASING: the
                startup sweep does, the dead-man switch leaves them to the periodic retry.
        """
        jobs = [
            Release(lease.lease_id, cause, graceful=False)
            for lease in self._deps.leases.all()
            if lease.lease_id not in self.busy
            and (include_releasing or lease.state is LeaseState.OPEN)
        ]
        await asyncio.gather(*(self.release_safely(job) for job in jobs))

    async def retry_releasing(self) -> None:
        """Try again every release that failed a step; say nothing unless one now succeeds."""
        for lease in self._deps.leases.all():
            if lease.state is LeaseState.RELEASING and lease.lease_id not in self.busy:
                cause = lease.release_cause or ReleaseCause.ORPHAN_SWEEP
                await self.release_safely(Release(lease.lease_id, cause, False, is_retry=True))

    async def release_safely(self, job: Release) -> bool:
        """Release one lease, logging instead of raising; used at the top of tasks.

        Args:
            job: The release to carry out.

        Returns:
            True if the device was left exactly as found.
        """
        try:
            return await self._release(job)
        except (PollenError, OSError) as error:
            log.error("pollen.release_failed lease=%s error=%s", job.lease_id, type(error).__name__)
        except Exception as error:  # SAFETY: task top; a bug must not end the packet's tasks.
            log.error("pollen.release_bug lease=%s error=%s", job.lease_id, type(error).__name__)
        return False

    async def stop_work(self, lease_id: LeaseId, *, graceful: bool, reason: str) -> int:
        """End what the executors track, then whatever the table still records for the lease.

        Args:
            lease_id: The lease whose work ends.
            graceful: Ask programs to terminate before forcing them.
            reason: Why, for the exits of the execs stopped.

        Returns:
            How many programs were still running.
        """
        killed = 0
        for stopper in self._stoppers:
            killed += await stopper.stop_lease(lease_id, graceful=graceful, reason=reason)
        lease = self._deps.leases.get(lease_id)
        for record in () if lease is None else lease.processes:
            ref = ProcessRef(pid=record.pid, created=record.created)
            if await stop_group(self._deps.shim, ref, graceful=graceful, clock=self._deps.clock):
                killed += 1
        return killed

    async def _release(self, job: Release) -> bool:
        """Run one attempt: stop, restore, remove scratch, report, and leave the table."""
        deps = self._deps
        self.busy.add(job.lease_id)
        try:
            await deps.leases.modify(job.lease_id, lambda lease: lease.releasing(job.cause))
            reason = f"The lease was released ({job.cause.value})."
            killed = await self.stop_work(job.lease_id, graceful=job.graceful, reason=reason)
            lease = deps.leases.get(job.lease_id)
            if lease is None:
                raise LeaseNotOpenError(f"Lease {job.lease_id} left the table during its release.")
            residual = await self._undo(lease)
            await self._report(lease, job, killed, residual)
            if not residual:
                await deps.leases.remove(job.lease_id)
            return not residual
        finally:
            self.busy.discard(job.lease_id)

    async def _undo(self, lease: DeviceLease) -> list[str]:
        """Put the recorded paths back and remove scratch; return what could not be undone."""
        residual = await asyncio.to_thread(replay_restores, self._deps.layout, lease)
        if not residual:
            # Recorded as done, so a crash before the table forgets the lease never replays them.
            await self._deps.leases.modify(
                lease.lease_id, lambda held: held.model_copy(update={"restores": ()})
            )
        if not await asyncio.to_thread(remove_scratch, Path(lease.scratch_root)):
            residual.append(lease.scratch_root)
        return residual

    async def _report(
        self, lease: DeviceLease, job: Release, killed: int, residual: list[str]
    ) -> None:
        """Queue the ``cell.lease_released`` that says how the release went."""
        if job.is_retry and residual:
            return  # Nothing new to say; the first attempt already reported the leftovers.
        released = LeaseReleased(
            lease_id=lease.lease_id,
            cell_id=lease.cell_id,
            holder=lease.holder,
            cause=job.cause,
            is_restored=not residual,
            killed_processes=killed,
            residual_paths=tuple(residual[:MAX_RESIDUAL_PATHS]),
            reason="The device was left as found." if not residual else "Some changes remain.",
        )
        target = job.reply_to
        hive = self._deps.state.current.hive_id
        await self._deps.reporter.send_durable(
            released,
            to=hive if target is None else target.sender,
            correlation_id=None if target is None else target.id,
        )


class LeaseService:
    """Opens, closes and releases leases for the dispatcher."""

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (
        CellRequest,
        CellTeardownRequest,
        SessionClose,
    )

    def __init__(self, deps: ExecutorDeps, stoppers: Sequence[LeaseStopper]) -> None:
        """Bind the service to the shared collaborators and to whatever tracks a lease's work.

        Args:
            deps: State, lease table, reporter, clock, platform shim, layout and task scope.
            stoppers: The executors that can end the work a lease started.
        """
        self._deps = deps
        self.releaser = Releaser(deps, stoppers)
        self._preparers: dict[type[WaggleMessage], Callable[[Envelope], Prepared]] = {
            CellRequest: self._prepare_open,
            CellTeardownRequest: self._prepare_teardown,
            SessionClose: self._prepare_close,
        }

    def prepare(self, envelope: Envelope) -> Prepared:
        """Read a lease request without changing anything.

        Args:
            envelope: A ``cell.request``, ``cell.teardown_request`` or ``session.close``.

        Returns:
            Nothing is needed beyond the sender rules; the work runs once the dispatcher awaits it.

        Raises:
            LeaseRefusedError: Another Cell or holder, a whole-Cell retirement, or too many leases.
            LeaseNotOpenError: The lease is not held, or is already being released.
        """
        return self._preparers[type(envelope.payload)](envelope)

    async def release_all(self, cause: ReleaseCause, *, include_releasing: bool) -> None:
        """Release every lease at once; see ``Releaser.release_all``."""
        await self.releaser.release_all(cause, include_releasing=include_releasing)

    async def retry_releasing(self) -> None:
        """Retry the releases that failed a step; see ``Releaser.retry_releasing``."""
        await self.releaser.retry_releasing()

    def _prepare_open(self, envelope: Envelope) -> Prepared:
        """Refuse a request for another Cell or holder, or one past the lease limit."""
        request = payload_of(envelope, CellRequest)
        state = self._deps.state.current
        if request.cell_id != state.cell_id or request.holder != state.warden_id:
            raise LeaseRefusedError(f"Request {envelope.id} names another Cell or holder.")
        if len(self._deps.leases.all()) >= MAX_OPEN_LEASES:
            raise LeaseRefusedError(f"Request {envelope.id} would pass the limit of open leases.")
        return Prepared(run=partial(self._open, envelope, request))

    async def _open(self, envelope: Envelope, request: CellRequest) -> None:
        """Create scratch, record the lease, and only then say it is open."""
        deps = self._deps
        state = deps.state.current
        lease_id = new_lease_id(deps.clock)
        scratch = await asyncio.to_thread(_make_scratch, deps.layout.scratch(lease_id), lease_id)
        quota = request.scratch_quota_bytes
        lease = DeviceLease(
            lease_id=lease_id,
            cell_id=state.cell_id,
            holder=state.warden_id,
            task_id=request.task_id,
            access_level=_lower(request.access_level, state.access_level),
            scratch_root=str(scratch),
            quota_bytes=DEFAULT_SCRATCH_QUOTA_BYTES if quota is None else quota,
            opened_at=deps.clock.now(),
        )
        await deps.leases.put(lease)  # Before the answer: a crash after it must find the lease.
        opened = LeaseOpened(
            lease_id=lease_id,
            cell_id=lease.cell_id,
            holder=lease.holder,
            task_id=lease.task_id,
            access_level=lease.access_level,
            comb_shield=state.comb_shield,
            scratch_root=lease.scratch_root,
            allowed_paths=_allowed_paths(state.granted_capabilities),
            dead_man_s=state.offline_limit_s,
            reason="The device's own gateway opened a lease on its Real Cell.",
        )
        await deps.reporter.send(opened, to=envelope.sender, correlation_id=envelope.id)

    def _prepare_teardown(self, envelope: Envelope) -> Prepared:
        """Refuse a whole-Cell retirement, another Cell, or a lease not held (or being released)."""
        request = payload_of(envelope, CellTeardownRequest)
        if request.lease_id is None:
            raise LeaseRefusedError(f"Request {envelope.id} would retire a Real Cell, never done.")
        if request.cell_id != self._deps.state.current.cell_id:
            raise LeaseRefusedError(f"Request {envelope.id} names another Cell.")
        held = self._deps.leases.get(request.lease_id)
        if held is None or request.lease_id in self.releaser.busy:
            raise LeaseNotOpenError(f"Lease {request.lease_id} is not held on this device.")
        urgency = request.urgency
        job = Release(request.lease_id, request.cause, urgency is Urgency.GRACEFUL, envelope)
        return Prepared(run=partial(self._start, job))

    async def _start(self, job: Release) -> None:
        """Run the release as a task of the packet, so the reader is never held up."""
        self._deps.scope.spawn(self._release_task(job), "release")

    async def _release_task(self, job: Release) -> None:
        """The body of a release task: the release, whose failure is logged and retried later."""
        await self.releaser.release_safely(job)

    def _prepare_close(self, envelope: Envelope) -> Prepared:
        """Refuse a close for a lease whose session is not open."""
        close = payload_of(envelope, SessionClose)
        self._deps.leases.require_session(close.lease_id)
        return Prepared(run=partial(self._close, close))

    async def _close(self, close: SessionClose) -> None:
        """Stop accepting session traffic, then end the session's programs in the background."""
        lease_id = close.lease_id
        await self._deps.leases.modify(
            lease_id, lambda lease: lease.model_copy(update={"is_session_closed": True})
        )
        self._deps.scope.spawn(self._end_session(lease_id), "session-close")

    async def _end_session(self, lease_id: LeaseId) -> None:
        """Kill every exec and background process of the lease; each exec reports KILLED."""
        reason = "The session was closed."
        with contextlib.suppress(PollenError, OSError):
            await self.releaser.stop_work(lease_id, graceful=False, reason=reason)


def _make_scratch(path: Path, lease_id: LeaseId) -> Path:
    """Create the lease's scratch directory (owner-only) and return its resolved path."""
    try:
        path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=False)
        return path.resolve(strict=True)
    except OSError as exc:
        raise LeaseRefusedError(f"Scratch for lease {lease_id} could not be created.") from exc


def _lower(requested: AccessLevel, granted: AccessLevel) -> AccessLevel:
    """Return the lower of the access level asked for and the node's own."""
    return requested if requested.rank <= granted.rank else granted


def _allowed_paths(capabilities: Sequence[str]) -> tuple[str, ...]:
    """Return the scopes of the ``cell:outside_scratch`` grants, within the wire bounds."""
    scopes = (
        scope
        for family, scope in filter(None, map(parse_capability, capabilities))
        if family == OUTSIDE_SCRATCH
    )
    return tuple(scope[:MAX_PATH_CHARS] for scope in scopes)[:MAX_ALLOWED_PATHS]
