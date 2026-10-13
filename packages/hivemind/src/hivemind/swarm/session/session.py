"""Provide PollenSession: a CellSession whose terminal is a Swarm device, reached over Waggle.

Every Cell offers a `CellSession`, the one way a Worker or a tool runs a command or touches a file
on it (codingrules section 8.7), and the Warden that drives a Real Cell does not know whether the
Cell is the Hive Stand or someone's laptop. `PollenSession` is the session for an enrolled device
at Level 0 of the runtime ladder, where only a Pollen Packet is on the device: the same calls,
carried as `session.*` messages over the device's link and answered by the packet. It is a thin
object. The work of each call lives beside it (`exec`, `files` and `process` in this package); this
class holds what is the session's own: whether it is open, the background processes it started
(the only ones it may stop or ask about), and the order of its end. Opening one sends
`session.open` and waits for the packet to say the lease's session is ready, so a session that
exists is a session the device agreed to. Closing one sends `session.close`, after which the packet
kills whatever the session still ran, and every later call raises `SessionClosedError`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Built by `SwarmSource.open_session` for the Warden that holds the lease; used, like every
    CellSession, by Worker tools. Calls into hivemind.cell (the session protocol and its errors),
    the gateway link, and the operations of this package.

Key invariants:
    - After `close()` returns, `is_open` is False, every call but `stop`/`is_running`/`close`
      raises `SessionClosedError`, and `stop`/`is_running` answer False without asking the device.
    - `stop` and `is_running` answer only for processes this session started.
    - Opening is the only way to get a session; an object whose device refused to open one is
      never returned.
    - Closing never raises for a lost link: the device's own dead-man switch or the lease's
      release then ends what the session started.

See Also:
    - docs/waggle/gateway-contract.md section 9.2 for the session messages.
    - tests/contracts/test_cell_session_contract.py for the behaviours this session mirrors.
    - hivemind.cell.local.session for the session of the Hive Stand's own Real Cell.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from hivemind.cell.errors import SessionClosedError
from hivemind.cell.lease import RealCellLease
from hivemind.cell.session import (
    BackgroundProcess,
    BackgroundSpec,
    ExecEvent,
    ExecSpec,
)
from hivemind.common.logging import get_logger
from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from hivemind.swarm.gateway.link import DeviceLink
from hivemind.swarm.session.exec import UNEXPECTED_REPLY_CODE, run_exec
from hivemind.swarm.session.files import delete_file, get_file, put_file
from hivemind.swarm.session.guard import CommandGuard
from hivemind.swarm.session.paths import DevicePaths
from hivemind.swarm.session.process import query_process, start_process, stop_process
from hivemind.swarm.session.wire import SessionWire, translated
from waggle.messages.session.commands import (
    SessionClose,
    SessionOpen,
    SessionOutcome,
    SessionRequestKind,
)
from waggle.messages.session.output import SessionExit

# Seconds to wait for the packet to open a lease's session: no work is done, it only agrees.
OPEN_REPLY_TIMEOUT_S = 30.0

OPEN_REASON = "The Warden opens the session on its lease."
CLOSE_REASON = "The Warden closed the session."

log = get_logger(__name__)

__all__ = ["CLOSE_REASON", "OPEN_REASON", "OPEN_REPLY_TIMEOUT_S", "PollenSession"]


class PollenSession:
    """A terminal on a Swarm device: exec, files, background processes, close."""

    def __init__(self, link: DeviceLink, lease: RealCellLease, guard: CommandGuard) -> None:
        """Wrap a device's link and an open lease; prefer `PollenSession.open`, which also opens.

        Args:
            link: The device's connection.
            lease: The lease on the device's Cell; its holder is the sender of every request.
            guard: Checks and records each command before it is sent.
        """
        self._wire = SessionWire(link, lease, guard, DevicePaths(lease))
        self._closed = False
        self._started: dict[int, BackgroundProcess] = {}

    @classmethod
    async def open(
        cls, link: DeviceLink, lease: RealCellLease, guard: CommandGuard
    ) -> PollenSession:
        """Open the lease's session on the device and return the session.

        Args:
            link: The device's connection.
            lease: An OPEN lease the device itself granted.
            guard: Checks and records each command before it is sent.

        Returns:
            A session the device has agreed to.

        Raises:
            SessionClosedError: The device no longer holds the lease open.
            GatewayUnavailableError: The link to the device is gone.
            DeviceTimeoutError: The device did not answer.
        """
        session = cls(link, lease, guard)
        wire = session._wire
        request = SessionOpen(lease_id=lease.id, reason=OPEN_REASON)
        async with link.exchange() as exchange:
            with translated(wire.error_context()):
                await exchange.send(request, sender=wire.sender)
                reply = await exchange.reply(OPEN_REPLY_TIMEOUT_S)
        payload = reply.payload
        if not (
            isinstance(payload, SessionExit)
            and payload.request_kind is SessionRequestKind.OPEN
            and payload.outcome is SessionOutcome.COMPLETED
        ):
            raise DeviceRequestError(UNEXPECTED_REPLY_CODE, "the device did not open the session.")
        return session

    @property
    def scratch_dir(self) -> Path:
        """The lease's scratch directory on the device; every relative path resolves under it."""
        return self._wire.lease.scratch_root

    @property
    def is_open(self) -> bool:
        """Whether the session can still be used: not closed, and its link still up."""
        return not self._closed and self._wire.link.is_open

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Run `spec` on the device and stream its output, ending in one `ExitStatus`.

        Args:
            spec: The command to run.

        Yields:
            `OutputChunk` events as output arrives, then one `ExitStatus`.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
            CommandDeniedError: The Guard (or the device) refused the command.
        """
        self._require_open()
        async for event in run_exec(self._wire, spec):
            yield event

    async def put_file(self, path: Path, data: bytes) -> None:
        """Write `data` to `path` on the device; see `CellSession.put_file`."""
        self._require_open()
        await put_file(self._wire, path, data)

    async def get_file(self, path: Path) -> bytes:
        """Read the bytes at `path` on the device; see `CellSession.get_file`."""
        self._require_open()
        return await get_file(self._wire, path)

    async def delete_file(self, path: Path) -> None:
        """Remove the file at `path` on the device; see `CellSession.delete_file`."""
        self._require_open()
        await delete_file(self._wire, path)

    async def start(self, spec: BackgroundSpec) -> BackgroundProcess:
        """Start `spec` in the background on the device; see `CellSession.start`."""
        self._require_open()
        process = await start_process(self._wire, spec)
        self._started[process.pid] = process
        # Also noted on the lease, so its release knows a process was started even though the
        # device, not the Hive Stand, is what kills it.
        self._wire.lease.note_started_process(process.pid)
        return process

    async def stop(self, process: BackgroundProcess) -> bool:
        """Stop a process this session started; False if it had ended or is not this session's."""
        if self._closed or process.pid not in self._started:
            return False
        return await stop_process(self._wire, process.pid)

    async def is_running(self, process: BackgroundProcess) -> bool:
        """Whether a process this session started is still running on the device."""
        if self._closed or process.pid not in self._started:
            return False
        return await query_process(self._wire, process.pid)

    async def close(self) -> None:
        """Close the session; the device then kills whatever it still ran. Idempotent."""
        if self._closed:
            return
        self._closed = True
        wire = self._wire
        try:
            await wire.link.send(
                SessionClose(lease_id=wire.lease.id, reason=CLOSE_REASON), sender=wire.sender
            )
        except (GatewayUnavailableError, DeviceTimeoutError):
            # The link is gone: the device's dead-man switch, or the lease's release when it
            # returns, ends what this session started.
            log.warning("swarm.session_close_unsent", lease_id=wire.lease.id)

    def _require_open(self) -> None:
        """Raise `SessionClosedError` unless the session has not been closed."""
        if self._closed:
            raise SessionClosedError(self.scratch_dir)
