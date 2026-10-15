"""Gateway conformance suite, part two: sessions, programs and background processes.

These tests state sections 9.2, 9.3 and 11 of the gateway contract: every session request ends in
exactly one `session.exit` or one `control.error`, an exec is an argument list run in its own
process group with its output chunked in order and its life bounded, and a background process is
recorded before it is answered and ends with its children. Part one, `test_gateway_contract.py`,
starts the gateway under test and documents how to point the suite at another one
(`HIVEMIND_GATEWAY_COMMAND`); this module shares its assumption that the gateway runs on this
machine.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises sections 9.2, 9.3 and 11
    of the gateway contract through `contracts.gateway`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - contracts.test_gateway_contract for the suite's overview and the command template.
    - docs/waggle/gateway-contract.md sections 9 and 11 for the clauses tested here.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from builders.swarm.live import LiveHive, LiveHiveOptions, start_live_hive
from contracts.gateway import (
    Gateway,
    alive,
    python_argv,
    refused,
    running_gateway,
    same_path,
    wait_until,
)

from hivemind.cell import AccessLevel
from waggle.messages.session import (
    ProcessAction,
    ProcessSignal,
    SessionOutcome,
    SessionOutput,
    SessionRequestKind,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")

HEARTBEAT_S = 5.0  # These tests need no quick beat; a quiet link keeps their frames easy to read.
_SLEEP_FOREVER = "import time; time.sleep(600)"  # A program that runs until it is killed.
# A program that starts a child in its group, prints the child's pid and waits.
_PARENT_WITH_CHILD = (
    "import subprocess, sys, time; "
    f"child = subprocess.Popen([sys.executable, '-c', {_SLEEP_FOREVER!r}]); "
    "print(child.pid, flush=True); time.sleep(600)"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def hive() -> AsyncIterator[LiveHive]:
    """A Hive Stand with the real listener."""
    stand = await start_live_hive(LiveHiveOptions(heartbeat_interval_s=HEARTBEAT_S))
    yield stand
    await stand.stop()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def gateway(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway enrolled with a FULL invite and online."""
    async with running_gateway(hive, tmp_path_factory.mktemp("full"), AccessLevel.FULL) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scratch(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway whose invite is capped at SCRATCH."""
    async with running_gateway(hive, tmp_path_factory.mktemp("sc"), AccessLevel.SCRATCH) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def watcher(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A FULL gateway reserved for the observation tests that change what it may watch."""
    async with running_gateway(hive, tmp_path_factory.mktemp("watch"), AccessLevel.FULL) as g:
        yield g


# ── Sessions and exec (sections 9.2 and 11) ───────────────────────────────────────


async def test_opening_a_session_is_answered_with_an_exit_of_kind_open(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        opened = await driver.open_session(lease)

    assert opened.request_kind is SessionRequestKind.OPEN
    assert opened.outcome is SessionOutcome.COMPLETED


async def test_session_traffic_for_a_lease_not_held_draws_lease_not_open(gateway: Gateway) -> None:
    driver = gateway.driver
    lease = await driver.open_lease()
    await driver.release(lease)

    assert (
        await refused(driver.run_exec(lease, python_argv("pass"))) == "hive.session.lease_not_open"
    )
    assert await refused(driver.open_session(lease)) == "hive.session.lease_not_open"


async def test_an_exec_streams_stdout_and_stderr_in_order_and_ends_in_one_exit(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    script = "import sys; sys.stdout.write('out'); sys.stdout.flush(); sys.stderr.write('err')"

    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv(script))
        await driver.sync()  # A chunk sent after the exit would have arrived by now.

    assert (run.stdout, run.stderr) == (b"out", b"err")
    assert run.is_well_formed()  # Offsets from 0 per stream, each ended by its own final chunk.
    assert (
        run.exit.request_kind is SessionRequestKind.EXEC and run.exit.request_id == run.request_id
    )
    assert (run.exit.outcome, run.exit.exit_code) == (SessionOutcome.EXITED, 0)
    late = [f for f in driver.frames_of(SessionOutput) if f.correlation_id == run.request_id]
    assert not late


async def test_the_exit_code_is_the_programs(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        seven = await driver.run_exec(lease, python_argv("import sys; sys.exit(7)"))

    assert (seven.exit.outcome, seven.exit.exit_code) == (SessionOutcome.EXITED, 7)


@pytest.mark.skipif(sys.platform == "win32", reason="no signal ends a program on Windows")
async def test_a_signal_that_ends_a_program_is_reported_by_name(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        killed = await driver.run_exec(
            lease, python_argv("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        )

    assert killed.exit.outcome is SessionOutcome.SIGNALED
    assert killed.exit.signal_name == "SIGKILL"


async def test_a_program_that_cannot_start_exits_127_with_a_line_on_stderr(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        run = await driver.run_exec(lease, ["/no/such/program-3f9a2c", "arg"])

    assert (run.exit.outcome, run.exit.exit_code) == (SessionOutcome.EXITED, 127)
    assert run.stderr and run.stdout == b""
    assert run.is_well_formed()


async def test_a_timeout_ends_the_whole_process_group_and_reports_timed_out(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv(_PARENT_WITH_CHILD), timeout_s=0.7)

    child = int(run.stdout.split()[0])
    assert run.exit.outcome is SessionOutcome.TIMED_OUT
    await wait_until(lambda: not alive(child), "the program's child to die with its group")


async def test_a_program_that_ignores_the_polite_request_is_forced_at_its_timeout(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    stubborn = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('up', flush=True); time.sleep(600)"
    )
    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv(stubborn), timeout_s=0.5)

    assert run.exit.outcome is SessionOutcome.TIMED_OUT


async def test_a_program_that_fills_scratch_past_the_quota_is_killed_and_the_reason_says_so(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    fill = (
        "import time\nwith open('fill.bin', 'wb') as out:\n    while True:\n"
        "        out.write(b'x' * 65536)\n        out.flush()\n        time.sleep(0.01)"
    )
    async with driver.leased(quota=200_000) as lease:
        run = await driver.run_exec(lease, python_argv(fill))

    assert run.exit.outcome is SessionOutcome.KILLED
    assert "quota" in run.exit.reason.lower()


async def test_a_stdin_signal_kills_a_running_exec_and_it_reports_killed(gateway: Gateway) -> None:
    driver = gateway.driver
    async with (
        driver.leased() as lease,
        driver.start_exec(lease, python_argv(f"print('up', flush=True); {_SLEEP_FOREVER}")) as r,
    ):
        await r.wait_for_output()
        await r.signal(ProcessSignal.KILL)
        run = await r.finish()

    assert run.exit.outcome is SessionOutcome.KILLED


async def test_stdin_arrives_in_order_and_the_final_chunk_closes_it(gateway: Gateway) -> None:
    driver = gateway.driver
    echo = "import sys; sys.stdout.write(sys.stdin.read().upper())"
    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv(echo), stdin=b"hello stdin")

    assert run.stdout == b"HELLO STDIN"
    assert run.exit.exit_code == 0


async def test_the_environment_overlay_and_the_default_directory_are_the_leases(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    show = "import os; print(os.environ['CONFORMANCE'], os.getcwd())"
    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv(show), env={"CONFORMANCE": "yes"})

    word, directory = run.stdout.decode().split()
    assert word == "yes"
    assert same_path(directory, lease.scratch_root)


async def test_an_argument_list_is_never_run_through_a_shell(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        run = await driver.run_exec(
            lease, python_argv("import sys; print(sys.argv[1])", "$HOME && echo pwned *")
        )

    assert run.stdout.decode().strip() == "$HOME && echo pwned *"


async def test_output_past_the_cap_kills_the_program_at_once(gateway: Gateway) -> None:
    driver = gateway.driver
    flood = "import sys\nwhile True:\n    sys.stdout.write('x' * 4096)\n    sys.stdout.flush()"
    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv(flood), max_output_bytes=10_000)

    assert run.exit.outcome is SessionOutcome.KILLED
    assert len(run.stdout) + len(run.stderr) <= 10_000


async def test_several_execs_run_at_once_in_one_session(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        runs = await asyncio.gather(
            *(
                driver.run_exec(lease, python_argv(f"import time; time.sleep(0.4); print({n})"))
                for n in range(3)
            )
        )

    assert sorted(run.stdout.strip() for run in runs) == [b"0", b"1", b"2"]
    assert len({run.request_id for run in runs}) == 3


async def test_closing_the_session_kills_what_runs_and_stops_session_traffic_only(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    lease = await driver.open_lease()
    async with driver.start_exec(
        lease, python_argv(f"print('up', flush=True); {_SLEEP_FOREVER}")
    ) as r:
        await r.wait_for_output()

        await driver.close_session(lease)
        run = await r.finish()

    assert run.exit.outcome is SessionOutcome.KILLED
    assert (
        await refused(driver.run_exec(lease, python_argv("pass"))) == "hive.session.lease_not_open"
    )
    released = await driver.release(lease)  # The lease itself stayed open until now.
    assert released.is_restored


# ── Background processes (section 9.3) ───────────────────────────────────────────────────


async def test_a_background_process_is_started_queried_and_stopped(gateway: Gateway) -> None:
    driver = gateway.driver
    argv = python_argv(_SLEEP_FOREVER)
    async with driver.leased() as lease:
        started = await driver.start_background(lease, argv)
        pid = started.pid
        assert started.is_running and started.argv == tuple(argv) and started.log_path is None
        assert alive(pid)

        queried = await driver.process(lease, ProcessAction.QUERY, pid=pid)
        stopped = await driver.process(lease, ProcessAction.STOP, pid=pid)
        after = await driver.process(lease, ProcessAction.QUERY, pid=pid)

    assert queried.is_running
    assert stopped.was_running is True and not stopped.is_running
    assert not after.is_running
    assert not alive(pid)


async def test_a_background_process_writes_to_a_log_inside_scratch(gateway: Gateway) -> None:
    driver = gateway.driver
    script = f"print('ready 99', flush=True); {_SLEEP_FOREVER}"
    async with driver.leased() as lease:
        started = await driver.start_background(lease, python_argv(script), log_path="out/bg.log")
        assert started.log_path is not None
        assert same_path(started.log_path, Path(lease.scratch_root) / "out" / "bg.log")

        async def logged() -> bool:
            data, _chunks, _closing = await driver.get_file(lease, "out/bg.log")
            return b"ready 99" in data

        deadline = time.monotonic() + 10
        while not await logged():
            assert time.monotonic() < deadline, "the process never wrote its log"
            await asyncio.sleep(0.05)


async def test_stopping_a_background_process_takes_its_children_with_it(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        started = await driver.start_background(
            lease, python_argv(_PARENT_WITH_CHILD), log_path="pids.log"
        )

        async def child_pid() -> int | None:
            data, _chunks, _closing = await driver.get_file(lease, "pids.log")
            return int(data.split()[0]) if data.split() else None

        deadline = time.monotonic() + 10
        while (child := await child_pid()) is None:
            assert time.monotonic() < deadline, "the process never printed its child's pid"
            await asyncio.sleep(0.05)
        assert alive(child)

        await driver.process(lease, ProcessAction.STOP, pid=started.pid)

    await wait_until(lambda: not alive(child), "the child to die with the process group")


async def test_a_pid_the_lease_did_not_start_is_never_signalled(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        for action in (ProcessAction.QUERY, ProcessAction.STOP):
            for pid in (os.getpid(), gateway.process.pid):
                assert await refused(driver.process(lease, action, pid=pid)) == "pollen.not_found"

    assert alive(os.getpid()) and alive(gateway.process.pid)


async def test_a_background_program_that_cannot_start_draws_start_failed(gateway: Gateway) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        code = await refused(driver.start_background(lease, ["/no/such/program-3f9a2c"]))

    assert code == "pollen.start_failed"


async def test_a_background_process_that_dies_on_its_own_is_reported_not_running(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    async with driver.leased() as lease:
        started = await driver.start_background(lease, python_argv(_SLEEP_FOREVER))
        # SIGTERM, which every host has: os.kill ends the process on Windows whatever the
        # signal, and the program installs no handler, so it dies at once on POSIX too.
        os.kill(started.pid, signal.SIGTERM)

        async def gone() -> bool:
            return not (
                await driver.process(lease, ProcessAction.QUERY, pid=started.pid)
            ).is_running

        deadline = time.monotonic() + 10
        while not await gone():
            assert time.monotonic() < deadline, "the dead process was still reported running"
            await asyncio.sleep(0.05)
        stopped = await driver.process(lease, ProcessAction.STOP, pid=started.pid)

    assert stopped.was_running is False
