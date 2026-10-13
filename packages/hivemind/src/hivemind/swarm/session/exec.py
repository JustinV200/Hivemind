"""Provide run_exec: run one command on a device and stream its output back, ending in one status.

This is `CellSession.exec` for a Swarm device. It resolves the command's working directory,
states what the command needs, asks the Guard and records `swarm.command_sent`, then sends one
`session.exec` and turns what the device streams back into the events a session promises: an
`OutputChunk` for each `session.output` frame in the order it arrived across both streams, and
exactly one `ExitStatus` at the end, or a raised `CommandTimeoutError` when the device reports the
command ran out of time. A command the device could not start is an ordinary failed command (exit
127 with a line on standard error, from the device), as for a local session. Standard input given
in the spec follows the request as `session.stdin` events, the last marked final. A command the
caller abandons (the generator is closed early, a task is cancelled, the device falls silent) is
sent a kill signal on the way out, best effort, so it does not run on after nobody is listening.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Called by `PollenSession.exec`. Calls into the session's wire, guard and code tables, the
    gateway link's exchange and waggle's session messages.

Key invariants:
    - The last event is always an `ExitStatus`, unless the generator raises first.
    - Nothing is sent before the Guard has authorised the command and the trail has recorded it.
    - The device is waited on for `timeout_s` plus a grace to report its own timeout; a device
      that outlasts even that is treated as having timed out, and sent a kill.
    - Output bytes, environment values and standard input are never logged or put on the trail.

See Also:
    - docs/waggle/gateway-contract.md section 11 for the exec semantics this relies on.
    - hivemind.cell.local.process for the local session this must behave like.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from hivemind.cell.errors import CommandTimeoutError
from hivemind.cell.session import ExecEvent, ExecSpec, ExitStatus, OutputChunk, OutputStream
from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from hivemind.swarm.gateway.exchange import Exchange
from hivemind.swarm.session.codes import ErrorContext, exit_code_for
from hivemind.swarm.session.guard import DeviceCommand
from hivemind.swarm.session.wire import SessionWire, command_for, exec_needs, translated
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.session.commands import (
    EnvVar,
    ProcessSignal,
    SessionExec,
    SessionOutcome,
    SessionStdin,
    SessionStream,
)
from waggle.messages.session.output import SessionExit, SessionOutput

# Seconds beyond `timeout_s` the device has to report its own TIMED_OUT before the Hive gives up
# waiting for it: killing a process group and reporting takes the packet a moment.
EXEC_REPORT_GRACE_S = 30.0

UNEXPECTED_REPLY_CODE = "hive.swarm.unexpected_reply"

__all__ = ["EXEC_REPORT_GRACE_S", "UNEXPECTED_REPLY_CODE", "run_exec"]

_STREAMS = {
    SessionStream.STDOUT: OutputStream.STDOUT,
    SessionStream.STDERR: OutputStream.STDERR,
}


@dataclass(frozen=True, slots=True)
class _Prepared:
    """One exec ready to send: the request, the command the Guard sees and the error context."""

    request: SessionExec
    command: DeviceCommand
    context: ErrorContext


async def run_exec(wire: SessionWire, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
    """Run `spec` on the device and stream its output, ending in exactly one `ExitStatus`.

    Args:
        wire: The session's link, lease, guard and path rules.
        spec: The command to run.

    Yields:
        `OutputChunk` events as the device's output arrives, then one `ExitStatus`.

    Raises:
        PathNotAllowedError: The working directory is outside scratch and every allowed path.
        CommandDeniedError: The Guard (or the device's own wall) refused the command.
        CommandTimeoutError: The command ran past `spec.timeout_s`.
        SessionClosedError: The device no longer holds the lease open.
        GatewayUnavailableError: The link to the device is gone.
    """
    prepared = _prepare(wire, spec)
    await wire.guard.authorise(prepared.command)
    async with wire.link.exchange() as exchange:
        await wire.guard.record_sent(prepared.command, exchange.id)
        await exchange.send(prepared.request, sender=wire.sender)
        finished = False
        try:
            with translated(prepared.context):
                if spec.stdin is not None:
                    await _feed_stdin(wire, exchange, spec.stdin)
                async for event in _events(exchange, spec):
                    yield event
                finished = True
        finally:
            # Reached with the command still running only if the caller walked away or something
            # failed after the request went out; the device would otherwise run it to its timeout.
            if not finished:
                await _abandon(wire, exchange)


def _prepare(wire: SessionWire, spec: ExecSpec) -> _Prepared:
    """Resolve the working directory, state the needs and build the request."""
    cwd = None if spec.cwd is None else wire.paths.resolve(spec.cwd)
    if cwd is not None:
        wire.paths.require_reach(cwd)
    needs = exec_needs(wire, spec.argv[0], cwd)
    request = SessionExec(
        lease_id=wire.lease.id,
        argv=spec.argv,
        cwd=cwd,
        env=tuple(EnvVar(name=name, value=value) for name, value in spec.env.items()),
        timeout_s=spec.timeout_s,
        has_stdin=spec.stdin is not None,
        task_id=wire.lease.task_id,
        worker_id=None,
    )
    return _Prepared(
        request=request,
        command=command_for(wire, "exec", needs, spec.argv),
        context=wire.error_context(path=cwd, needed=str(needs[0])),
    )


async def _events(exchange: Exchange, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
    """Turn the device's frames for one exec into session events, ending at its exit."""
    try:
        async for envelope in exchange.events(spec.timeout_s + EXEC_REPORT_GRACE_S):
            payload = envelope.payload
            if isinstance(payload, SessionOutput):
                stream = _STREAMS.get(payload.stream)
                if stream is not None and payload.chunk:
                    yield OutputChunk(stream=stream, data=payload.chunk)
            elif isinstance(payload, SessionExit):
                for event in _ending(payload, spec):
                    yield event
                # Not a return: the exchange's own iterator ends after this terminal frame, and
                # letting the loop reach that end leaves no half-read generator behind.
            else:
                raise DeviceRequestError(
                    UNEXPECTED_REPLY_CODE,
                    f"the device answered an exec with {type(payload).__name__}.",
                )
    except DeviceTimeoutError as exc:
        # The device kept silent past its own deadline plus the grace: report what a local
        # session would, and let the caller's cleanup kill whatever is still running.
        raise CommandTimeoutError(spec.argv, spec.timeout_s) from exc


def _ending(exit_message: SessionExit, spec: ExecSpec) -> Iterator[ExecEvent]:
    """Yield the closing events of an exec: a note on a device kill, then the status."""
    if exit_message.outcome is SessionOutcome.TIMED_OUT:
        raise CommandTimeoutError(spec.argv, spec.timeout_s)
    if exit_message.outcome is SessionOutcome.KILLED and exit_message.reason:
        # The device ended it (a quota, the output cap, a session close); say so where the bee
        # that proposed the command reads failures, as a shell reports a command it could not run.
        note = f"killed by the device: {exit_message.reason}\n".encode()
        yield OutputChunk(stream=OutputStream.STDERR, data=note)
    yield ExitStatus(code=exit_code_for(exit_message), duration_s=exit_message.duration_s)


async def _feed_stdin(wire: SessionWire, exchange: Exchange, data: bytes) -> None:
    """Send `data` as `session.stdin` events, the last one closing the process's stdin."""
    pieces = [
        data[start : start + MAX_CHUNK_BYTES] for start in range(0, len(data), MAX_CHUNK_BYTES)
    ]
    offset = 0
    # An empty input still sends one final chunk, because the device holds stdin open until told.
    for index, piece in enumerate(pieces or [b""]):
        last = index == max(len(pieces) - 1, 0)
        message = SessionStdin(
            lease_id=wire.lease.id,
            exec_id=exchange.id,
            chunk=piece,
            offset=offset,
            final=last,
            signal=None,
        )
        await exchange.send(message, sender=wire.sender, correlation_id=exchange.id)
        offset += len(piece)


async def _abandon(wire: SessionWire, exchange: Exchange) -> None:
    """Ask the device to kill an exec nobody waits for; the link may be gone, so best effort."""
    kill = SessionStdin(
        lease_id=wire.lease.id,
        exec_id=exchange.id,
        chunk=b"",
        offset=0,
        final=False,
        signal=ProcessSignal.KILL,
    )
    try:
        await exchange.send(kill, sender=wire.sender, correlation_id=exchange.id)
    except (GatewayUnavailableError, DeviceTimeoutError):
        return  # The device's own timeout or dead-man switch ends it.
