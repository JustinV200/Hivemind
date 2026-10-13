"""Tests for hivemind.swarm.session.exec: running a command on a device through PollenSession.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/exec.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The exec clauses of tests/contracts/test_cell_session_contract.py are repeated
    here against the scripted device, because PollenSession is not registered in that suite.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.exec for the module under test.
    - builders.swarm.device for the scripted Pollen Packet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from builders.swarm import (
    DeviceScript,
    ExecScript,
    LinkRig,
    RecordingGuard,
    make_heartbeat,
    open_rig_session,
)

from hivemind.cell.errors import CommandTimeoutError, PathNotAllowedError, SessionClosedError
from hivemind.cell.session import ExecSpec, ExitStatus, OutputChunk, OutputStream, run
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError, GatewayUnavailableError
from hivemind.swarm.session import exec as exec_module
from hivemind.swarm.session.codes import KILLED_EXIT_CODE
from hivemind.swarm.session.exec import UNEXPECTED_REPLY_CODE
from waggle.envelope import Envelope
from waggle.messages.session.commands import SessionExec, SessionStdin
from waggle.messages.swarm import DeviceHeartbeat

RigFactory = Callable[[DeviceScript], Awaitable[LinkRig]]


def _a_heartbeat(rig: LinkRig) -> DeviceHeartbeat:
    """A frame that is never a valid answer to a session request."""
    return make_heartbeat(rig.party)


_SCRIPT = DeviceScript(
    execs={
        "echo": ExecScript(stdout=b"hello"),
        "both": ExecScript(stdout=b"hello", stderr=b"boom"),
        "nonzero": ExecScript(exit_code=7),
        "missing": ExecScript(unstartable=True),
        "sleep": ExecScript(hang=True),
        "killed": ExecScript(hang=True),
        "signalled": ExecScript(signal_name="SIGKILL"),
        "chunky": ExecScript(stdout=b"abcdef", output_chunks=3),
    }
)


async def test_exec_ends_in_exactly_one_exit_status_after_its_output(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    events = [event async for event in session.exec(ExecSpec(argv=("both",)))]

    assert isinstance(events[-1], ExitStatus)
    assert all(not isinstance(event, ExitStatus) for event in events[:-1])


async def test_run_collects_stdout_and_stderr_on_success(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    completed = await run(session, ExecSpec(argv=("both",)))

    assert (completed.exit_code, completed.stdout, completed.stderr) == (0, b"hello", b"boom")


async def test_a_nonzero_exit_code_surfaces(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    completed = await run(session, ExecSpec(argv=("nonzero",)))

    assert completed.exit_code == 7


async def test_a_command_that_cannot_start_is_a_failed_command_not_an_exception(
    rigs: RigFactory,
) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    completed = await run(session, ExecSpec(argv=("missing",)))

    assert completed.exit_code == 127
    assert completed.stderr


async def test_output_chunks_arrive_in_order(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    events = [event async for event in session.exec(ExecSpec(argv=("chunky",)))]

    chunks = [event.data for event in events if isinstance(event, OutputChunk)]
    assert b"".join(chunks) == b"abcdef"
    assert len(chunks) == 3
    assert all(
        event.stream is OutputStream.STDOUT for event in events if isinstance(event, OutputChunk)
    )


async def test_a_timeout_raises_command_timeout(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    with pytest.raises(CommandTimeoutError):
        await run(session, ExecSpec(argv=("sleep",), timeout_s=0.3))


async def test_a_signal_death_is_a_negative_exit_code(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    completed = await run(session, ExecSpec(argv=("signalled",)))

    assert completed.exit_code == -9


async def test_a_command_the_device_killed_says_so_on_stderr(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)

    async def kill_when_started() -> None:
        await asyncio.wait_for(rig.device.state.exec_started.wait(), 5.0)
        for exec_id in tuple(rig.device.state.execs):
            rig.device.state.kill(exec_id)

    killer = asyncio.ensure_future(kill_when_started())
    completed = await run(session, ExecSpec(argv=("killed",), timeout_s=5.0))
    await killer

    assert completed.exit_code == KILLED_EXIT_CODE
    assert b"killed by the device" in completed.stderr


async def test_the_request_carries_the_command_and_never_runs_through_a_shell(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, lease, _guard = await open_rig_session(rig)

    await run(session, ExecSpec(argv=("echo", "a b", "$HOME"), env={"MODE": "x"}))

    sent = [e.payload for e in rig.device.state.received if isinstance(e.payload, SessionExec)]
    assert [(p.argv, p.lease_id, p.cwd) for p in sent] == [
        (("echo", "a b", "$HOME"), lease.id, None)
    ]
    assert [(v.name, v.value) for v in sent[0].env] == [("MODE", "x")]


async def test_standard_input_reaches_the_program_and_is_closed(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)

    await run(session, ExecSpec(argv=("echo",), stdin=b"secret input"))

    stdin_frames = [
        e.payload for e in rig.device.state.received if isinstance(e.payload, SessionStdin)
    ]
    assert b"".join(frame.chunk for frame in stdin_frames) == b"secret input"
    assert stdin_frames[-1].final is True


async def test_an_exec_after_close_raises_session_closed(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))
    await session.close()

    with pytest.raises(SessionClosedError):
        await run(session, ExecSpec(argv=("echo",)))


async def test_a_relative_cwd_resolves_under_scratch(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, lease, _guard = await open_rig_session(rig)

    await run(session, ExecSpec(argv=("echo",), cwd=Path("sub/dir")))

    sent = [e.payload for e in rig.device.state.received if isinstance(e.payload, SessionExec)]
    assert sent[0].cwd == f"{lease.scratch_root}/sub/dir"


async def test_a_cwd_outside_scratch_and_the_allowed_paths_is_refused_before_sending(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)

    with pytest.raises(PathNotAllowedError):
        await run(session, ExecSpec(argv=("echo",), cwd=Path("/etc")))

    assert not [e for e in rig.device.state.received if isinstance(e.payload, SessionExec)]


async def test_the_guard_is_asked_and_the_command_recorded_before_the_frame_is_sent(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, lease, guard = await open_rig_session(rig)

    await run(session, ExecSpec(argv=("/usr/bin/echo", "one", "two")))

    (command,) = guard.authorised[-1:]
    assert (command.operation, command.program, command.argument_count) == ("exec", "echo", 2)
    assert [str(need) for need in command.needs] == ["exec:/usr/bin/echo"]
    recorded, request_id = guard.sent[-1]
    sent = [e for e in rig.device.state.received if isinstance(e.payload, SessionExec)]
    assert (recorded, request_id) == (command, sent[0].id)
    assert command.lease_id == lease.id


async def test_a_denied_command_sends_nothing(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    guard = RecordingGuard(deny=frozenset({"exec:rm"}))
    session, _lease, _ = await open_rig_session(rig, guard)

    with pytest.raises(CommandDeniedError):
        await run(session, ExecSpec(argv=("rm", "-rf", "/")))

    assert not [e for e in rig.device.state.received if isinstance(e.payload, SessionExec)]
    assert guard.sent == []


async def test_a_cwd_outside_scratch_needs_the_outside_scratch_capability(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, lease, guard = await open_rig_session(rig, allowed_paths=(Path("/srv/work"),))

    await run(session, ExecSpec(argv=("echo",), cwd=Path("/srv/work/job")))

    command = guard.authorised[-1]
    assert [str(need) for need in command.needs] == [
        "exec:echo",
        "cell:outside_scratch:/srv/work/job",
    ]
    assert command.outside_scratch is True
    assert lease.id == command.lease_id


async def test_an_empty_program_name_is_a_denial_not_a_crash(rigs: RigFactory) -> None:
    session, _lease, _guard = await open_rig_session(await rigs(_SCRIPT))

    with pytest.raises(CommandDeniedError):
        await run(session, ExecSpec(argv=("",)))


async def test_a_cancelled_caller_kills_the_running_command(rigs: RigFactory) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)

    caller = asyncio.ensure_future(run(session, ExecSpec(argv=("sleep",), timeout_s=30.0)))
    await asyncio.wait_for(rig.device.state.exec_started.wait(), 5.0)
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)

    await asyncio.wait_for(rig.device.state.kill_seen.wait(), 5.0)
    assert rig.device.state.killed


async def test_a_device_that_sends_something_else_in_answer_to_an_exec_is_an_error(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)

    async def wrong_answer(request: Envelope) -> None:
        await rig.device.send_raw(_a_heartbeat(rig), request.id)

    rig.device.override(SessionExec, wrong_answer)

    with pytest.raises(DeviceRequestError) as caught:
        await run(session, ExecSpec(argv=("echo",)))

    assert caught.value.device_code == UNEXPECTED_REPLY_CODE


async def test_a_device_that_stays_silent_past_the_deadline_and_grace_is_a_timeout(
    rigs: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exec_module, "EXEC_REPORT_GRACE_S", 0.05)
    rig = await rigs(DeviceScript(execs=_SCRIPT.execs, silent={SessionExec}))
    session, _lease, _guard = await open_rig_session(rig)

    with pytest.raises(CommandTimeoutError):
        await run(session, ExecSpec(argv=("echo",), timeout_s=0.05))


async def test_cancelling_a_command_whose_link_has_gone_does_not_raise_from_the_kill(
    rigs: RigFactory,
) -> None:
    rig = await rigs(_SCRIPT)
    session, _lease, _guard = await open_rig_session(rig)
    caller = asyncio.ensure_future(run(session, ExecSpec(argv=("sleep",), timeout_s=30.0)))
    await asyncio.wait_for(rig.device.state.exec_started.wait(), 5.0)

    await rig.link.close()  # The kill has nowhere to go.
    caller.cancel()
    results = await asyncio.gather(caller, return_exceptions=True)

    assert isinstance(results[0], asyncio.CancelledError | GatewayUnavailableError)
