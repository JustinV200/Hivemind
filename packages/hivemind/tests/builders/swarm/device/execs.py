"""Answer `session.exec` and `session.stdin` the way a Pollen Packet does, from a script.

A request to run a program draws `session.output` chunks on each stream, each stream ending with a
chunk marked final, and then exactly one `session.exit` (gateway contract section 11). Which
program does what is the device's `ExecScript` for its name: output, an exit code, a hang until the
timeout, a signal, or a program that cannot start (one line on standard error and exit 127). Each
exec runs as a task so one that hangs never blocks the next request; a `session.stdin` signal or a
session close kills it (KILLED), and its timeout ends it TIMED_OUT. Standard input the Hive sends
is kept so a test can read what reached the program.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the scripted device.

Key invariants:
    - Every exec ends in exactly one `session.exit`, after its output and never before.
    - A request for a lease that is not open, or whose session is not open, draws
      `hive.session.lease_not_open`.
    - Output is real wall-clock-free: only a scripted hang waits, and only up to its timeout.

See Also:
    - docs/waggle/gateway-contract.md section 11 for the semantics mimicked.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import Awaitable, Callable, Sequence

from builders.swarm.device.context import DeviceContext
from builders.swarm.device.leases import LEASE_NOT_OPEN
from builders.swarm.device.state import ExecScript

from waggle.envelope import Envelope
from waggle.messages.base import MAX_CHUNK_BYTES, WaggleMessage
from waggle.messages.session.commands import (
    ProcessSignal,
    SessionExec,
    SessionOutcome,
    SessionRequestKind,
    SessionStdin,
    SessionStream,
)
from waggle.messages.session.output import SessionExit, SessionOutput

EXIT_COMMAND_NOT_STARTED = 127

__all__ = ["EXIT_COMMAND_NOT_STARTED", "ExecService"]

Handler = Callable[[Envelope], Awaitable[None]]


def _script_for(scripts: dict[str, ExecScript], argv: Sequence[str]) -> ExecScript:
    """The script for the whole command line if one is given, else for the program's name."""
    return scripts.get(" ".join(argv)) or scripts.get(posixpath.basename(argv[0])) or ExecScript()


class ExecService:
    """The scripted device's exec behaviour."""

    def __init__(self, ctx: DeviceContext) -> None:
        """Bind the service to a device context."""
        self._ctx = ctx

    def handlers(self) -> dict[type[WaggleMessage], Handler]:
        """The request types this service answers, each with its handler."""
        return {SessionExec: self._start, SessionStdin: self._stdin}

    async def _start(self, request: Envelope) -> None:
        """Start the scripted program as a task, or refuse a lease whose session is not open."""
        asked = request.payload
        assert isinstance(asked, SessionExec)
        state = self._ctx.state
        lease = state.leases.get(asked.lease_id)
        if lease is None or not lease.session_open:
            await self._ctx.error(request, LEASE_NOT_OPEN, "That lease has no open session.")
            return
        script = _script_for(state.script.execs, asked.argv)
        state.kill_events[request.id] = asyncio.Event()
        state.execs[request.id] = asyncio.ensure_future(self._run(request, asked, script))
        state.exec_started.set()

    async def _stdin(self, request: Envelope) -> None:
        """Keep the bytes sent to an exec, and honour a kill signal."""
        asked = request.payload
        assert isinstance(asked, SessionStdin)
        state = self._ctx.state
        state.stdin[asked.exec_id] = state.stdin.get(asked.exec_id, b"") + asked.chunk
        if asked.signal is ProcessSignal.KILL:
            state.kill(asked.exec_id)

    async def _run(self, request: Envelope, asked: SessionExec, script: ExecScript) -> None:
        """Play one exec: its output, then its one exit."""
        state = self._ctx.state
        try:
            if script.unstartable:
                await self._output(request, asked, SessionStream.STDERR, b"not found\n", 1)
                await self._exit(request, asked, SessionOutcome.EXITED, EXIT_COMMAND_NOT_STARTED)
                return
            await self._output(
                request, asked, SessionStream.STDOUT, script.stdout, script.output_chunks
            )
            await self._output(
                request, asked, SessionStream.STDERR, script.stderr, script.output_chunks
            )
            await self._finish(request, asked, script)
        finally:
            state.execs.pop(request.id, None)
            state.kill_events.pop(request.id, None)

    async def _finish(self, request: Envelope, asked: SessionExec, script: ExecScript) -> None:
        """Send the exit the script calls for: a hang resolves at its timeout or a kill."""
        if script.hang:
            outcome = await self._hang(request.id, asked.timeout_s)
            await self._exit(request, asked, outcome, None)
        elif script.signal_name is not None:
            await self._exit(request, asked, SessionOutcome.SIGNALED, None, script.signal_name)
        else:
            await self._exit(request, asked, SessionOutcome.EXITED, script.exit_code)

    async def _hang(self, exec_id: str, timeout_s: float) -> SessionOutcome:
        """Wait for a kill signal or the timeout, and say which came first."""
        event = self._ctx.state.kill_events[exec_id]
        try:
            await asyncio.wait_for(event.wait(), timeout_s)
        except TimeoutError:
            return SessionOutcome.TIMED_OUT
        return SessionOutcome.KILLED

    async def _output(
        self,
        request: Envelope,
        asked: SessionExec,
        stream: SessionStream,
        data: bytes,
        chunks: int,
    ) -> None:
        """Send one stream's bytes as `chunks` chunks, the last marked final (empty if no data)."""
        # No chunk is ever longer than the protocol allows, whatever the script asked for.
        size = min(max(-(-len(data) // chunks), 1), MAX_CHUNK_BYTES)
        pieces = [data[start : start + size] for start in range(0, len(data), size)] or [b""]
        offset = 0
        for index, piece in enumerate(pieces):
            await self._ctx.reply(
                request,
                SessionOutput(
                    lease_id=asked.lease_id,
                    request_id=request.id,
                    stream=stream,
                    chunk=piece,
                    offset=offset,
                    final=index == len(pieces) - 1,
                ),
            )
            offset += len(piece)

    async def _exit(
        self,
        request: Envelope,
        asked: SessionExec,
        outcome: SessionOutcome,
        code: int | None,
        signal_name: str | None = None,
    ) -> None:
        """Send the exec's one `session.exit`."""
        await self._ctx.reply(
            request,
            SessionExit(
                lease_id=asked.lease_id,
                request_id=request.id,
                request_kind=SessionRequestKind.EXEC,
                outcome=outcome,
                exit_code=code if outcome is SessionOutcome.EXITED else None,
                signal_name=signal_name,
                duration_s=0.0,
                size_bytes=None,
                sha256=None,
                reason=f"Scripted {outcome.value}.",
            ),
        )
