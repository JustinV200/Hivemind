"""Tests for hivemind.entrance.expose.tunnel: the supervised tunnel client, with a real child.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/tunnel.py (codingrules section 3). The child is always
    the test's own Python interpreter running a few lines (harmless, on every platform); the
    backoff and the stop grace period run on a FakeClock that reports each sleep it is asked
    for, so no test waits on a timer. A real child still takes real milliseconds to start and
    exit, so those waits are bounded by ``_REAL_WAIT_S``.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.tunnel for the module under test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest
import structlog
from pydantic import SecretStr
from unit.entrance.expose.support import START

from hivemind.entrance.expose import ExposureRefusedError, TunnelSupervisor, tunnel_environment
from hivemind.entrance.expose.tunnel import (
    INITIAL_RESTART_DELAY_S,
    KILL_WAIT_S,
    MAX_RESTART_DELAY_S,
    STABLE_RUN_S,
    STOP_GRACE_S,
)
from waggle.clock import FakeClock, SystemClock

TOKEN = "tunnel-token-that-must-never-be-logged"  # noqa: S105 -- a test value, not a credential
_REAL_WAIT_S = 20.0  # A child interpreter starts in tens of milliseconds; this is a hang.
_POLL_S = 0.01  # How often a test looks for a file a child writes.
_EXIT_AT_ONCE = "raise SystemExit(3)"
_SLEEP = "import time; time.sleep(3600)"
_WRITE_ENVIRONMENT = (
    "import json, os, sys; open(sys.argv[1], 'w').write(json.dumps(dict(os.environ)))"
)
# Exits at once on its first two runs and stays up on the third, counting runs in a file.
_THIRD_RUN_STAYS = (
    "import pathlib, sys, time\n"
    "counter = pathlib.Path(sys.argv[1])\n"
    "runs = int(counter.read_text()) if counter.exists() else 0\n"
    "counter.write_text(str(runs + 1))\n"
    "if runs == 2:\n"
    "    time.sleep(3600)\n"
    "raise SystemExit(3)\n"
)
_IGNORE_SIGTERM = (
    "import pathlib, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "pathlib.Path(sys.argv[1]).touch()\n"
    "time.sleep(3600)\n"
)


class _ReportingClock(FakeClock):
    """A FakeClock that queues each sleep it is asked for, so a test can wait for the next."""

    def __init__(self) -> None:
        """Start at START with no sleeps requested."""
        super().__init__(START)
        self.requests: asyncio.Queue[float] = asyncio.Queue()

    async def sleep(self, seconds: float) -> None:
        """Report the request, then sleep on fake time."""
        self.requests.put_nowait(seconds)
        await super().sleep(seconds)


def _python(script: str, *args: str) -> list[str]:
    """Argv running ``script`` in this interpreter: a harmless child on every platform."""
    return [sys.executable, "-c", script, *args]


def _environment() -> dict[str, str]:
    """The child's environment as the Entrance builds it, with one tunnel token."""
    return tunnel_environment(os.environ, {"TUNNEL_TOKEN": SecretStr(TOKEN)})


async def _next_sleep(clock: _ReportingClock) -> float:
    """Wait (on real time, bounded) for the supervisor's next sleep request."""
    async with asyncio.timeout(_REAL_WAIT_S):
        return await clock.requests.get()


async def _running_pid(supervisor: TunnelSupervisor) -> int:
    """Wait (bounded) for a child to be running and return its pid."""
    async with asyncio.timeout(_REAL_WAIT_S):
        await supervisor.wait_running()
    pid = supervisor.pid
    assert pid is not None
    return pid


async def _until(predicate: Callable[[], bool]) -> None:
    """Poll ``predicate`` until it holds, failing after ``_REAL_WAIT_S``.

    A real child's own start-up cannot run on a FakeClock (codingrules 14.5's "no sleeping in
    tests" is for logical waits; this coordinates with a real process).
    """
    deadline = time.monotonic() + _REAL_WAIT_S
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"The condition did not hold within {_REAL_WAIT_S}s.")
        await asyncio.sleep(_POLL_S)


def test_tunnel_environment_drops_the_hives_variables_and_adds_the_tunnels() -> None:
    hive = {"PATH": "/usr/bin", "HIVEMIND_ANTHROPIC_API_KEY": "sk", "hivemind_db": "x.db"}

    environment = tunnel_environment(
        hive, {"TUNNEL_TOKEN": SecretStr(TOKEN), "PATH": SecretStr("/opt")}
    )

    assert environment == {"PATH": "/opt", "TUNNEL_TOKEN": TOKEN}


def test_tunnel_environment_withholds_provider_keys_named_without_the_hive_prefix() -> None:
    hive = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk", "openai_api_key": "sk", "LANG": "C"}

    environment = tunnel_environment(hive, {}, withheld=("ANTHROPIC_API_KEY", "OPENAI_API_KEY"))

    assert environment == {"PATH": "/usr/bin", "LANG": "C"}


def test_the_supervisor_refuses_an_argv_without_a_program() -> None:
    with pytest.raises(ExposureRefusedError, match="tunnel_command_missing"):
        TunnelSupervisor([], {}, FakeClock())
    with pytest.raises(ExposureRefusedError):
        TunnelSupervisor(["", "run"], {}, FakeClock())


def test_repr_never_shows_the_arguments_or_the_environment() -> None:
    supervisor = TunnelSupervisor(["bore", f"--secret={TOKEN}"], _environment(), FakeClock())

    assert TOKEN not in repr(supervisor)
    assert "bore" in repr(supervisor)


async def test_the_child_gets_its_environment_and_nothing_is_logged_of_it(tmp_path: Path) -> None:
    clock = _ReportingClock()
    written = tmp_path / "environment.json"
    # The Hive's own secret sits in its environment; the tunnel child must never see it.
    hive_environ = {**os.environ, "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY": "vapid-never-passed"}
    environment = tunnel_environment(hive_environ, {"TUNNEL_TOKEN": SecretStr(TOKEN)})
    supervisor = TunnelSupervisor(
        _python(_WRITE_ENVIRONMENT, str(written), f"--mistaken-token={TOKEN}"), environment, clock
    )

    with structlog.testing.capture_logs() as captured:
        async with asyncio.TaskGroup() as group:
            group.create_task(supervisor.run())
            await _next_sleep(clock)
            await supervisor.stop()

    child = json.loads(written.read_text())
    names = {name.upper() for name in child}
    assert child["TUNNEL_TOKEN"] == TOKEN
    assert "PATH" in names
    assert not any(name.startswith("HIVEMIND_") for name in names)
    assert "entrance.tunnel_exited" in {entry["event"] for entry in captured}
    assert TOKEN not in repr(captured)


async def test_an_exiting_child_is_restarted_with_capped_exponential_backoff() -> None:
    clock = _ReportingClock()
    supervisor = TunnelSupervisor(_python(_EXIT_AT_ONCE), _environment(), clock)
    delays: list[float] = []

    async with asyncio.TaskGroup() as group:
        group.create_task(supervisor.run())
        # Each exit asks for the next pause; advancing past it starts the child again.
        for _ in range(8):
            delay = await _next_sleep(clock)
            delays.append(delay)
            clock.advance(delay)
        # A ninth pause means the eighth restart ran (and its child exited) too.
        delays.append(await _next_sleep(clock))
        await supervisor.stop()

    assert (
        delays
        == [INITIAL_RESTART_DELAY_S * 2**step for step in range(6)] + [MAX_RESTART_DELAY_S] * 3
    )
    assert supervisor.restarts == 8


async def test_a_child_that_ran_stably_restarts_without_backoff(tmp_path: Path) -> None:
    clock = _ReportingClock()
    supervisor = TunnelSupervisor(
        _python(_THIRD_RUN_STAYS, str(tmp_path / "runs")), _environment(), clock
    )

    async with asyncio.TaskGroup() as group:
        group.create_task(supervisor.run())
        first = await _next_sleep(clock)
        clock.advance(first)
        second = await _next_sleep(clock)
        clock.advance(second)
        # The third run stays up for a minute of fake time, then dies of its own accord.
        pid = await _running_pid(supervisor)
        clock.advance(STABLE_RUN_S)
        psutil.Process(pid).terminate()
        third = await _next_sleep(clock)
        await supervisor.stop()

    assert (first, second, third) == (1.0, 2.0, INITIAL_RESTART_DELAY_S)


async def test_stop_ends_the_running_child_and_nothing_restarts() -> None:
    clock = _ReportingClock()
    supervisor = TunnelSupervisor(_python(_SLEEP), _environment(), clock)

    async with asyncio.TaskGroup() as group:
        runner = group.create_task(supervisor.run())
        pid = await _running_pid(supervisor)
        await supervisor.stop()

    assert runner.done()
    assert not psutil.pid_exists(pid)
    assert supervisor.pid is None
    assert supervisor.restarts == 0


async def test_stop_during_a_start_still_ends_the_child_that_start_made() -> None:
    supervisor = TunnelSupervisor(_python(_SLEEP), _environment(), _ReportingClock())

    async with asyncio.TaskGroup() as group:
        group.create_task(supervisor.run())
        # One turn of the loop: run() is now inside the spawn, where stop() cannot see a child.
        await asyncio.sleep(0)
        await supervisor.stop()
        survivors = [child for child in psutil.Process().children() if _SLEEP in child.cmdline()]

    assert survivors == []
    assert supervisor.pid is None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to ignore.")
async def test_stop_kills_a_child_that_ignores_sigterm_after_the_grace_period(
    tmp_path: Path,
) -> None:
    clock = _ReportingClock()
    ready = tmp_path / "ready"
    supervisor = TunnelSupervisor(_python(_IGNORE_SIGTERM, str(ready)), _environment(), clock)

    with structlog.testing.capture_logs() as captured:
        async with asyncio.TaskGroup() as group:
            group.create_task(supervisor.run())
            pid = await _running_pid(supervisor)
            await _until(ready.exists)
            stopping = group.create_task(supervisor.stop())
            # The child shrugs off SIGTERM; only the grace period running out ends it.
            assert await _next_sleep(clock) == STOP_GRACE_S
            assert psutil.pid_exists(pid)
            clock.advance(STOP_GRACE_S)
            await stopping

    assert not psutil.pid_exists(pid)
    assert "entrance.tunnel_killed" in {entry["event"] for entry in captured}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to ignore.")
async def test_stop_is_bounded_on_the_real_clock_as_the_reducer_needs(tmp_path: Path) -> None:
    # The Entrance Reducer cuts the door within a second (ADR-0041): even a child that ignores
    # SIGTERM must be gone that fast. Twice the bound leaves room for a loaded host.
    ready = tmp_path / "ready"
    supervisor = TunnelSupervisor(
        _python(_IGNORE_SIGTERM, str(ready)), _environment(), SystemClock()
    )

    async with asyncio.TaskGroup() as group:
        group.create_task(supervisor.run())
        pid = await _running_pid(supervisor)
        await _until(ready.exists)
        started = time.monotonic()
        await supervisor.stop()
        elapsed = time.monotonic() - started

    assert elapsed < 2 * (STOP_GRACE_S + KILL_WAIT_S)
    assert not psutil.pid_exists(pid)


async def test_cancelling_run_ends_the_child_before_the_cancellation_propagates() -> None:
    supervisor = TunnelSupervisor(_python(_SLEEP), _environment(), _ReportingClock())

    async with asyncio.TaskGroup() as group:
        runner = group.create_task(supervisor.run())
        pid = await _running_pid(supervisor)
        runner.cancel()

    assert runner.cancelled()
    assert not psutil.pid_exists(pid)


async def test_a_missing_program_is_retried_and_logged_without_its_arguments(
    tmp_path: Path,
) -> None:
    clock = _ReportingClock()
    missing = str(tmp_path / "no-such-tunnel-client")
    supervisor = TunnelSupervisor([missing, f"--token={TOKEN}"], _environment(), clock)

    with structlog.testing.capture_logs() as captured:
        async with asyncio.TaskGroup() as group:
            group.create_task(supervisor.run())
            delay = await _next_sleep(clock)
            await supervisor.stop()

    assert delay == INITIAL_RESTART_DELAY_S
    failures = [entry for entry in captured if entry["event"] == "entrance.tunnel_start_failed"]
    assert failures and failures[0]["program"] == missing
    assert TOKEN not in repr(captured)


async def test_stop_before_run_starts_nothing() -> None:
    clock = _ReportingClock()
    supervisor = TunnelSupervisor(_python(_SLEEP), _environment(), clock)

    await supervisor.stop()
    # run() returns at once: it never spawned, so it never paused on the (never advanced) clock.
    await supervisor.run()

    assert supervisor.pid is None
    assert supervisor.restarts == 0
