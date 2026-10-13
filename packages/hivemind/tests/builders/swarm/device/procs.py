"""Answer `session.process` the way a Pollen Packet does: start, stop and query a fake process.

A background process on the scripted device is an entry in its table with a fake pid. `START`
records it before answering (the contract's rule), writes the program's scripted log to the log
path when one was given, and answers a `session.process_state` with the pid, the command line and
the resolved log path; a program the script lists as failing to start draws `pollen.start_failed`.
`STOP` marks the process not running and says in `was_running` whether it was; `QUERY` says whether
it is. A pid the lease did not start draws `pollen.not_found`, so one lease can never signal
another's process. A test can end a process on its own with `DeviceState.processes`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the scripted device.

Key invariants:
    - A process is recorded before its START is answered.
    - A STOP or QUERY for a pid the lease did not start is `pollen.not_found`.
    - A log path outside scratch is refused with `pollen.path_refused`.

See Also:
    - docs/waggle/gateway-contract.md section 9.3 for the behaviour mimicked.
"""

from __future__ import annotations

import posixpath
from collections.abc import Awaitable, Callable

from builders.swarm.device.context import DeviceContext
from builders.swarm.device.files import NOT_FOUND, PATH_REFUSED
from builders.swarm.device.leases import LEASE_NOT_OPEN
from builders.swarm.device.state import DeviceProcess

from waggle.envelope import Envelope
from waggle.messages.base import WaggleMessage
from waggle.messages.session.process import ProcessAction, SessionProcess, SessionProcessState

START_FAILED = "pollen.start_failed"

__all__ = ["START_FAILED", "ProcessService"]

Handler = Callable[[Envelope], Awaitable[None]]


class ProcessService:
    """The scripted device's background-process behaviour."""

    def __init__(self, ctx: DeviceContext) -> None:
        """Bind the service to a device context."""
        self._ctx = ctx

    def handlers(self) -> dict[type[WaggleMessage], Handler]:
        """The request types this service answers, each with its handler."""
        return {SessionProcess: self._handle}

    async def _handle(self, request: Envelope) -> None:
        """Dispatch a `session.process` request by its action."""
        asked = request.payload
        assert isinstance(asked, SessionProcess)
        lease = self._ctx.state.leases.get(asked.lease_id)
        if lease is None or not lease.session_open:
            await self._ctx.error(request, LEASE_NOT_OPEN, "That lease has no open session.")
        elif asked.action is ProcessAction.START:
            await self._start(request, asked)
        else:
            await self._pid_action(request, asked)

    async def _start(self, request: Envelope, asked: SessionProcess) -> None:
        """Record a new process, write its scripted log, and answer with its state."""
        state = self._ctx.state
        lease = state.leases[asked.lease_id]
        program = posixpath.basename(asked.argv[0])
        if program in state.script.start_failures:
            await self._ctx.error(request, START_FAILED, "The program could not be started.")
            return
        log = None if asked.log_path is None else state.resolve(lease, asked.log_path)
        if log is not None and not (
            log == lease.scratch_root or log.startswith(lease.scratch_root + "/")
        ):
            await self._ctx.error(request, PATH_REFUSED, "A log must be inside scratch.")
            return
        process = DeviceProcess(state.new_pid(), lease.lease_id, asked.argv, log)
        state.processes[process.pid] = process
        if log is not None:
            state.files[log] = state.script.start_logs.get(program, b"")
        await self._ctx.reply(
            request,
            SessionProcessState(
                lease_id=lease.lease_id,
                action=ProcessAction.START,
                pid=process.pid,
                argv=asked.argv,
                log_path=log,
                is_running=True,
                was_running=None,
                reason="Started.",
            ),
        )

    async def _pid_action(self, request: Envelope, asked: SessionProcess) -> None:
        """Answer a STOP or QUERY for a process this lease started."""
        process = self._ctx.state.processes.get(asked.pid or 0)
        if process is None or process.lease_id != asked.lease_id:
            await self._ctx.error(request, NOT_FOUND, "The lease did not start that process.")
            return
        stopping = asked.action is ProcessAction.STOP
        was_running = process.running
        if stopping:
            process.running = False
        await self._ctx.reply(
            request,
            SessionProcessState(
                lease_id=asked.lease_id,
                action=asked.action,
                pid=process.pid,
                argv=(),
                log_path=None,
                is_running=process.running,
                was_running=was_running if stopping else None,
                reason="Stopped." if stopping else "Queried.",
            ),
        )
