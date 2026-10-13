"""Provide start_process, stop_process and query_process: background programs on a device.

These are the background-process half of `CellSession` for a Swarm device (ADR-0031): a display
server, a browser, later a model server on a Nuc, started in a process group of their own and
left running until stopped or until the lease is released. `start_process` resolves the working
directory and the log path (the log must be inside scratch), states the capabilities the launch
needs (the program, the directory if it is outside scratch, a write to the log), asks the Guard and
records `swarm.command_sent`, then sends one `session.process` START and returns the process the
device reports. `stop_process` and `query_process` name a pid the session itself started: a stop is
a recorded command that needs no capability, a query is a read that sends and records nothing but
the request. A pid the device says its lease did not start (`pollen.not_found`) is simply not
running, so a stop of it is a quiet False, as for a local session.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Called by `PollenSession.start`, `stop` and `is_running`. Calls into the session's wire, guard
    and code tables, the gateway link's exchange and waggle's session messages.

Key invariants:
    - Nothing is sent before the Guard has authorised a START and the trail has recorded it.
    - A log path outside scratch is refused here, before anything is sent: a background program's
      output never lands outside the lease's scratch directory.
    - The returned process is exactly what the device reported: its pid, its command line and the
      resolved log path it opened.
    - Command lines, environment values and paths are never logged or put on the trail.

See Also:
    - docs/waggle/gateway-contract.md section 9.3 for the process messages.
    - hivemind.cell.session.CellSession for the contract these functions satisfy.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell.session import BackgroundProcess, BackgroundSpec
from hivemind.swarm.errors import DeviceRequestError
from hivemind.swarm.gateway.exchange import Exchange
from hivemind.swarm.session.codes import NOT_FOUND
from hivemind.swarm.session.exec import UNEXPECTED_REPLY_CODE
from hivemind.swarm.session.wire import (
    SessionWire,
    command_for,
    exec_needs,
    translated,
    write_needs,
)
from waggle.envelope import Envelope
from waggle.messages.session.commands import EnvVar
from waggle.messages.session.process import ProcessAction, SessionProcess, SessionProcessState

# Seconds to wait for a process request's single answer: a launch or a signal is quick work.
PROCESS_REPLY_TIMEOUT_S = 30.0

__all__ = ["PROCESS_REPLY_TIMEOUT_S", "query_process", "start_process", "stop_process"]


async def start_process(wire: SessionWire, spec: BackgroundSpec) -> BackgroundProcess:
    """Start `spec` on the device in the background and return the process it reports.

    Args:
        wire: The session's link, lease, guard and path rules.
        spec: The command to start.

    Returns:
        The started process: its pid, command line and the resolved log path it opened.

    Raises:
        PathNotAllowedError: The working directory is out of reach, or the log is outside scratch.
        CommandDeniedError: The Guard (or the device's own wall) refused the launch.
        BackgroundStartError: The device could not start the program.
        GatewayUnavailableError: The link to the device is gone.
    """
    cwd = None if spec.cwd is None else wire.paths.resolve(spec.cwd)
    if cwd is not None:
        wire.paths.require_reach(cwd)
    log = None if spec.log_path is None else wire.paths.resolve(spec.log_path)
    if log is not None:
        wire.paths.require_inside_scratch(log)
    needs = exec_needs(wire, spec.argv[0], cwd) + (() if log is None else write_needs(wire, log))
    command = command_for(wire, "start", needs, spec.argv)
    await wire.guard.authorise(command)
    request = SessionProcess(
        lease_id=wire.lease.id,
        action=ProcessAction.START,
        argv=spec.argv,
        cwd=cwd,
        env=tuple(EnvVar(name=name, value=value) for name, value in spec.env.items()),
        log_path=log,
        pid=None,
        task_id=wire.lease.task_id,
        worker_id=None,
    )
    async with wire.link.exchange() as exchange:
        await wire.guard.record_sent(command, exchange.id)
        with translated(wire.error_context(argv=spec.argv, needed=str(needs[0]))):
            await exchange.send(request, sender=wire.sender)
            reply = await exchange.reply(PROCESS_REPLY_TIMEOUT_S)
    state = _expect_state(reply, ProcessAction.START)
    return BackgroundProcess(
        pid=state.pid,
        argv=state.argv,
        log_path=None if state.log_path is None else Path(state.log_path),
    )


async def stop_process(wire: SessionWire, pid: int) -> bool:
    """Stop the background process `pid` and its group, and say whether it was running.

    Args:
        wire: The session's link, lease, guard and path rules.
        pid: A process this session started.

    Returns:
        True when the device found it running and stopped it; False when it had already ended or
        the device does not know the pid.

    Raises:
        GatewayUnavailableError: The link to the device is gone.
    """
    command = command_for(wire, "stop", ())
    await wire.guard.authorise(command)
    request = _pid_request(wire, ProcessAction.STOP, pid)
    async with wire.link.exchange() as exchange:
        await wire.guard.record_sent(command, exchange.id)
        state = await _ask(wire, exchange, request)
    return bool(state is not None and state.was_running)


async def query_process(wire: SessionWire, pid: int) -> bool:
    """Ask whether the background process `pid` is still running.

    Args:
        wire: The session's link, lease, guard and path rules.
        pid: A process this session started.

    Returns:
        True while the device reports it running; False once it has ended or is unknown.

    Raises:
        GatewayUnavailableError: The link to the device is gone.
    """
    request = _pid_request(wire, ProcessAction.QUERY, pid)
    async with wire.link.exchange() as exchange:
        state = await _ask(wire, exchange, request)
    return bool(state is not None and state.is_running)


def _pid_request(wire: SessionWire, action: ProcessAction, pid: int) -> SessionProcess:
    """Build the request of a stop or a query: only an action and a pid."""
    return SessionProcess(
        lease_id=wire.lease.id,
        action=action,
        argv=(),
        cwd=None,
        env=(),
        log_path=None,
        pid=pid,
        task_id=wire.lease.task_id,
        worker_id=None,
    )


async def _ask(
    wire: SessionWire, exchange: Exchange, request: SessionProcess
) -> SessionProcessState | None:
    """Send a stop or query and return the device's state, or None for a pid it does not know."""
    with translated(wire.error_context()):
        try:
            await exchange.send(request, sender=wire.sender)
            reply = await exchange.reply(PROCESS_REPLY_TIMEOUT_S)
        except DeviceRequestError as error:
            # A pid the lease did not start (or that is long gone) is simply not running; every
            # other refusal is left to `translated`, which maps the codes a session promises.
            if error.device_code == NOT_FOUND:
                return None
            raise
    return _expect_state(reply, request.action)


def _expect_state(envelope: Envelope, action: ProcessAction) -> SessionProcessState:
    """Return the `session.process_state` that answers `action`, or raise if it is anything else."""
    payload = envelope.payload
    if not isinstance(payload, SessionProcessState) or payload.action is not action:
        raise DeviceRequestError(
            UNEXPECTED_REPLY_CODE, f"the device did not answer the {action.value} request."
        )
    return payload
