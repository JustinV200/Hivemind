"""Run programs on a gateway under test, in the foreground or behind it, and ask it to observe.

An exec (section 11 of the gateway contract) is an argument list run in the lease's own process
group whose output comes back as ordered chunks and whose end is exactly one `session.exit`.
`run_exec` sends one and returns an `ExecRun`, everything the gateway answered with; `start_exec`
hands back a `RunningExec` while the program runs, to feed its standard input or signal it. A
background process (section 9.3) is started, queried and stopped by `process`, and an observation
(section 9.4) is asked for with `observe`. What an exec may ask beyond its arguments is the
`ExecOptions` keywords, so a call names only what the test is about.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. A part of
    `contracts.gateway.Driver`; builds on `contracts.gateway.calls`.

Key invariants:
    - An exec's frames are read to its exit or until `timeout_s + EXEC_SLACK_S`; a gateway that
      never ends one is a failed test, not a hang.
    - The driver adds no behaviour of its own: the frame it sends is the one the contract names.

See Also:
    - docs/waggle/gateway-contract.md sections 9.3, 9.4 and 11 for the clauses checked with this.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict, Unpack

from contracts.gateway.calls import Calls

from waggle.envelope import Envelope
from waggle.ids import DeviceId, MessageId
from waggle.messages.base import DEFAULT_MAX_OUTPUT_BYTES
from waggle.messages.cell import LeaseOpened
from waggle.messages.session import (
    EnvVar,
    ProcessAction,
    ProcessSignal,
    SessionExec,
    SessionExit,
    SessionOutput,
    SessionProcess,
    SessionProcessState,
    SessionStdin,
    SessionStream,
)
from waggle.messages.swarm import Observation, ObservationKind, Observe

EXEC_SLACK_S = 20.0  # Beyond an exec's own timeout, for the gateway to kill it and report.
DEFAULT_EXEC_TIMEOUT_S = 30.0  # The exec timeout a test gets unless it names one.

__all__ = ["ExecOptions", "ExecRun", "ProgramCalls", "RunningExec"]


class ExecOptions(TypedDict, total=False):
    """What an exec may ask beyond its arguments; each is left to the contract when absent."""

    timeout_s: float  # Seconds before the gateway ends the program (30 s here by default).
    env: Mapping[str, str]  # Variables laid over the lease's own environment.
    cwd: str  # The working directory; the lease's scratch directory when absent.
    stdin: bytes  # Standard input, written whole and closed.
    max_output_bytes: int  # The cap on output before the program is killed.


@dataclass(frozen=True, slots=True)
class ExecRun:
    """Everything one exec drew from the gateway: its output chunks and the closing exit."""

    request_id: MessageId
    frames: tuple[Envelope, ...]

    @property
    def exit(self) -> SessionExit:
        """The one `session.exit` that ended the run: the last frame."""
        last = self.frames[-1].payload
        assert isinstance(last, SessionExit), f"the run did not end in an exit: {last!r}"
        return last

    def chunks(self, stream: SessionStream) -> list[SessionOutput]:
        """The chunks of one stream, in arrival order."""
        return [
            frame.payload
            for frame in self.frames
            if isinstance(frame.payload, SessionOutput) and frame.payload.stream is stream
        ]

    def data(self, stream: SessionStream) -> bytes:
        """The bytes of one stream, chunks joined in arrival order."""
        return b"".join(chunk.chunk for chunk in self.chunks(stream))

    @property
    def stdout(self) -> bytes:
        """Everything the program wrote to standard output."""
        return self.data(SessionStream.STDOUT)

    @property
    def stderr(self) -> bytes:
        """Everything the program wrote to standard error."""
        return self.data(SessionStream.STDERR)

    def is_well_formed(self) -> bool:
        """Whether each stream's offsets count from 0 and the stream ends with one final chunk."""
        for stream in (SessionStream.STDOUT, SessionStream.STDERR):
            chunks = self.chunks(stream)
            offsets = [chunk.offset for chunk in chunks]
            expected = [sum(len(c.chunk) for c in chunks[:i]) for i in range(len(chunks))]
            finals = [chunk.final for chunk in chunks]
            if offsets != expected or finals != [False] * (len(chunks) - 1) + [True]:
                return False
        return True


class RunningExec:
    """An exec that has been sent and not yet finished: signal it, feed it, then `finish`."""

    def __init__(self, line: Calls, lease: LeaseOpened, exchange_id: MessageId) -> None:
        """Wrap a started exec; `ProgramCalls.start_exec` is the way to get one."""
        self._line = line
        self._lease = lease
        self.id = exchange_id
        self._frames: list[Envelope] = []
        self._events: AsyncIterator[Envelope] | None = None

    def attach(self, events: AsyncIterator[Envelope]) -> None:
        """Remember the iterator of the exchange's frames."""
        self._events = events

    async def signal(self, signal: ProcessSignal) -> None:
        """Deliver a signal to the program instead of bytes."""
        await self._send_stdin(b"", final=False, signal=signal)

    async def feed(self, data: bytes, *, final: bool = True) -> None:
        """Send `data` to the program's standard input, closing it when `final`."""
        await self._send_stdin(data, final=final, signal=None)

    async def wait_for_output(self) -> None:
        """Read frames until the program has written something: it is running for certain."""
        assert self._events is not None
        async for envelope in self._events:
            self._frames.append(envelope)
            if isinstance(envelope.payload, SessionOutput) and envelope.payload.chunk:
                return

    async def finish(self) -> ExecRun:
        """Read every frame up to the exit."""
        assert self._events is not None
        async for envelope in self._events:
            self._frames.append(envelope)
        return ExecRun(self.id, tuple(self._frames))

    async def _send_stdin(self, data: bytes, *, final: bool, signal: ProcessSignal | None) -> None:
        """Send one `session.stdin` correlated to this exec."""
        message = SessionStdin(
            lease_id=self._lease.lease_id,
            exec_id=self.id,
            chunk=data,
            offset=0,
            final=final,
            signal=signal,
        )
        await self._line.link.send(message, sender=self._line.warden, correlation_id=self.id)


class ProgramCalls(Calls):
    """The requests that run programs, start background processes and ask for observations."""

    async def run_exec(
        self, lease: LeaseOpened, argv: Sequence[str], **options: Unpack[ExecOptions]
    ) -> ExecRun:
        """Run a program in the lease and return every frame it drew, up to its exit."""
        async with self._started(lease, argv, options) as run:
            return await run.finish()

    @asynccontextmanager
    async def start_exec(
        self, lease: LeaseOpened, argv: Sequence[str], **options: Unpack[ExecOptions]
    ) -> AsyncIterator[RunningExec]:
        """Send an exec and hand back control while it runs, to signal it or feed it."""
        async with self._started(lease, argv, options) as run:
            yield run

    @asynccontextmanager
    async def _started(
        self, lease: LeaseOpened, argv: Sequence[str], options: ExecOptions
    ) -> AsyncIterator[RunningExec]:
        """Open the exchange for one exec, send it (and its stdin) and yield the running exec."""
        timeout_s = options.get("timeout_s", DEFAULT_EXEC_TIMEOUT_S)
        stdin = options.get("stdin")
        request = SessionExec(
            lease_id=lease.lease_id,
            argv=tuple(argv),
            cwd=options.get("cwd"),
            env=tuple(EnvVar(name=n, value=v) for n, v in options.get("env", {}).items()),
            timeout_s=timeout_s,
            has_stdin=stdin is not None,
            task_id=None,
            worker_id=None,
            max_output_bytes=options.get("max_output_bytes") or DEFAULT_MAX_OUTPUT_BYTES,
        )
        async with self.link.exchange() as exchange:
            await exchange.send(request, sender=self.warden)
            running = RunningExec(self, lease, exchange.id)
            running.attach(exchange.events(timeout_s + EXEC_SLACK_S))
            if stdin is not None:
                await running.feed(stdin)
            yield running

    async def process(
        self,
        lease: LeaseOpened,
        action: ProcessAction,
        *,
        argv: Sequence[str] = (),
        pid: int | None = None,
        log_path: str | None = None,
    ) -> SessionProcessState:
        """Start, stop or query a background process and return the gateway's state report."""
        request = SessionProcess(
            lease_id=lease.lease_id,
            action=action,
            argv=tuple(argv),
            cwd=None,
            env=(),
            log_path=log_path,
            pid=pid,
            task_id=None,
            worker_id=None,
        )
        answer = (await self.ask(request)).payload
        assert isinstance(answer, SessionProcessState)
        return answer

    async def start_background(
        self, lease: LeaseOpened, argv: Sequence[str], *, log_path: str | None = None
    ) -> SessionProcessState:
        """Start a background program in the lease and return the state the gateway reports."""
        return await self.process(lease, ProcessAction.START, argv=argv, log_path=log_path)

    def observation_request(
        self,
        kinds: Sequence[ObservationKind],
        *,
        roots: Sequence[str] = (),
        since_at: datetime | None = None,
        max_items: int = 200,
        device_id: DeviceId | None = None,
    ) -> Observe:
        """Build a `swarm.observe` for this device (or, to be refused, for another)."""
        return Observe(
            device_id=device_id or self.node.device_id,
            kinds=tuple(kinds),
            roots=tuple(roots),
            since_at=since_at,
            max_items=max_items,
            reason="The conformance suite watches the device.",
        )

    async def observe(
        self,
        kinds: Sequence[ObservationKind],
        *,
        roots: Sequence[str] = (),
        since_at: datetime | None = None,
        max_items: int = 200,
    ) -> Observation:
        """Ask the gateway to observe its device and return the `swarm.observation`."""
        request = self.observation_request(
            kinds, roots=roots, since_at=since_at, max_items=max_items
        )
        answer = (await self.ask(request)).payload
        assert isinstance(answer, Observation)
        return answer
