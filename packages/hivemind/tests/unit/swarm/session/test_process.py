"""Tests for hivemind.swarm.session.process: background programs on a device.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/process.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The background-process clauses of tests/contracts/test_cell_session_contract.py
    are repeated here against the scripted device, because PollenSession is not registered there.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.process for the module under test.
    - builders.swarm.device.procs for the device's side of a process.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from builders.swarm import (
    DeviceScript,
    LinkRig,
    RecordingGuard,
    make_heartbeat,
    open_rig_session,
)

from hivemind.cell.errors import BackgroundStartError, PathNotAllowedError, SessionClosedError
from hivemind.cell.session import BackgroundProcess, BackgroundSpec
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError
from hivemind.swarm.session.codes import NOT_FOUND
from hivemind.swarm.session.exec import UNEXPECTED_REPLY_CODE
from waggle.envelope import Envelope
from waggle.messages.session.process import ProcessAction, SessionProcess

RigFactory = Callable[[DeviceScript], Awaitable[LinkRig]]

_SCRIPT = DeviceScript(start_failures={"missing"}, start_logs={"logs": b"ready 99\n"})


async def test_start_returns_a_running_process_and_stop_ends_it(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    process = await session.start(BackgroundSpec(argv=("runs",)))

    assert process.pid > 0
    assert await session.is_running(process)
    assert await session.stop(process) is True
    assert not await session.is_running(process)
    assert await session.stop(process) is False  # Idempotent: stopping twice is a quiet False.
    await session.close()


async def test_a_background_process_writes_its_log_inside_scratch(rigs: RigFactory) -> None:
    session, lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    process = await session.start(BackgroundSpec(argv=("logs",), log_path=Path("x/out.log")))

    # The rig's device is POSIX, so its paths are spelled with `/` whatever this host is.
    assert process.log_path == Path(f"{lease.scratch_root.as_posix()}/x/out.log")
    assert await session.get_file(Path("x/out.log")) == b"ready 99\n"


async def test_a_log_path_outside_scratch_is_refused_before_anything_is_sent(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig, allowed_paths=(Path("/pollen/logs"),))

    with pytest.raises(PathNotAllowedError):
        await session.start(BackgroundSpec(argv=("runs",), log_path=Path("/pollen/logs/x.log")))

    assert not [e for e in rig.device.state.received if isinstance(e.payload, SessionProcess)]


async def test_a_command_that_cannot_start_raises_background_start_error(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    with pytest.raises(BackgroundStartError):
        await session.start(BackgroundSpec(argv=("missing",)))


async def test_is_running_turns_false_when_the_process_dies_on_its_own(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)
    process = await session.start(BackgroundSpec(argv=("runs",)))

    rig.device.state.processes[process.pid].running = False

    assert not await session.is_running(process)
    assert await session.stop(process) is False  # It had already exited; still reaped quietly.


async def test_close_stops_every_background_process(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)
    first = await session.start(BackgroundSpec(argv=("runs",)))
    second = await session.start(BackgroundSpec(argv=("runs",)))

    await session.close()
    await asyncio.wait_for(rig.device.state.session_closed.wait(), 5.0)

    assert not await session.is_running(first)
    assert not await session.is_running(second)
    assert not any(process.running for process in rig.device.state.processes.values())


async def test_a_session_answers_only_for_processes_it_started(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)
    stranger = BackgroundProcess(pid=999, argv=("runs",), log_path=None)

    assert await session.stop(stranger) is False
    assert await session.is_running(stranger) is False
    assert not [
        e
        for e in rig.device.state.received
        if isinstance(e.payload, SessionProcess) and e.payload.pid == 999
    ]


async def test_start_after_close_raises_session_closed(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))
    await session.close()

    with pytest.raises(SessionClosedError):
        await session.start(BackgroundSpec(argv=("runs",)))


async def test_the_start_is_authorised_and_recorded_with_the_program_only(rigs: RigFactory) -> None:
    session, lease, guard = await open_rig_session(await rigs(_SCRIPT))

    await session.start(BackgroundSpec(argv=("/usr/bin/runs", "--flag", "x"), log_path=Path("l")))

    command, _request_id = guard.sent[-1]
    assert (command.operation, command.program, command.argument_count) == ("start", "runs", 2)
    assert [str(need) for need in command.needs] == [
        "exec:/usr/bin/runs",
        f"fs:write:{lease.scratch_root.as_posix()}/l",
    ]


async def test_a_denied_start_sends_nothing(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _ = await open_rig_session(rig, RecordingGuard(deny=frozenset({"exec:runs"})))

    with pytest.raises(CommandDeniedError):
        await session.start(BackgroundSpec(argv=("runs",)))

    assert not [e for e in rig.device.state.received if isinstance(e.payload, SessionProcess)]


async def test_a_stop_is_recorded_and_needs_no_capability_and_a_query_is_not_recorded(
    rigs: RigFactory,
) -> None:
    session, _lease, guard = await open_rig_session(await rigs(_SCRIPT))
    process = await session.start(BackgroundSpec(argv=("runs",)))
    recorded_before = len(guard.sent)

    await session.is_running(process)
    assert len(guard.sent) == recorded_before
    await session.stop(process)

    command, _request_id = guard.sent[-1]
    assert (command.operation, command.needs) == ("stop", ())


async def test_the_started_pid_is_noted_on_the_lease(rigs: RigFactory) -> None:
    session, lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    process = await session.start(BackgroundSpec(argv=("runs",)))

    assert lease.started_pids == (process.pid,)


async def test_the_environment_and_working_directory_go_to_the_device(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, lease, _guard = await open_rig_session(rig)

    await session.start(BackgroundSpec(argv=("runs",), cwd=Path("w"), env={"A": "1"}))

    (sent,) = [
        e.payload for e in rig.device.state.received if isinstance(e.payload, SessionProcess)
    ]
    assert sent.action is ProcessAction.START
    assert sent.cwd == f"{lease.scratch_root.as_posix()}/w"
    assert [(v.name, v.value) for v in sent.env] == [("A", "1")]


async def test_a_pid_the_device_no_longer_knows_is_simply_not_running(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript())  # Its own script: the refusal below must not leak.
    session, _lease, _guard = await open_rig_session(rig)
    process = await session.start(BackgroundSpec(argv=("runs",)))
    rig.device.script.refusals[SessionProcess] = NOT_FOUND  # It has been forgotten since.

    assert await session.is_running(process) is False
    assert await session.stop(process) is False


async def test_a_device_that_answers_a_query_with_something_else_is_an_error(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)
    process = await session.start(BackgroundSpec(argv=("runs",)))

    async def wrong_answer(request: Envelope) -> None:
        await rig.device.send_raw(make_heartbeat(rig.party), request.id)

    rig.device.override(SessionProcess, wrong_answer)

    with pytest.raises(DeviceRequestError) as caught:
        await session.is_running(process)

    assert caught.value.device_code == UNEXPECTED_REPLY_CODE
