"""Test the platform seam's shared pieces: running a service command and stopping a process group.

The Pollen Packet (the thin gateway program on a device the Hive borrows) differs from host to
host in only a few ways, and the rest of pollen holds a ``PlatformShim`` instead of looking at the
operating system. The pieces every shim shares live in ``pollen.platform.base``: ``run_command``
runs one service-manager command as an argument list, never through a shell, and reports a program
that is missing as a status rather than an exception; ``launch_command`` is the argv a service
unit runs; ``stop_group`` ends a whole process group, politely first when asked and by force
after a short grace, so a program's children never outlive it. These tests run real programs.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.base.

Key invariants:
    - A command that cannot run is a status of 127, never an exception.
    - A stopped group has no live member afterwards, even one that ignored the polite signal.

See Also:
    - docs/waggle/gateway-contract.md sections 3 and 11.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from fast_clock import FastClock

from pollen.platform import (
    CommandResult,
    LinuxShim,
    ProcessRef,
    SpawnSpec,
    launch_command,
)
from pollen.platform.base import COMMAND_UNAVAILABLE, run_command, stop_group

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="process groups")
TERM_PROOF = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
)


def test_a_command_reports_its_status_and_output() -> None:
    result = run_command([sys.executable, "-c", "print('hello'); raise SystemExit(3)"])
    assert (result.returncode, result.output) == (3, "hello")


def test_a_command_that_is_not_there_is_a_status_not_an_exception() -> None:
    assert run_command(["/definitely/not/a/program"]) == CommandResult(COMMAND_UNAVAILABLE, "")


def test_a_command_that_hangs_is_reported_after_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pollen.platform.base.COMMAND_TIMEOUT_S", 0.2)
    assert run_command([sys.executable, "-c", "import time; time.sleep(30)"]).returncode == 127


def test_the_launch_command_runs_this_interpreter_as_a_module(tmp_path: Path) -> None:
    command = launch_command(tmp_path)
    assert command == (sys.executable, "-m", "pollen", "run", "--data-dir", str(tmp_path))


def test_a_frozen_build_launches_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert launch_command(tmp_path) == (sys.executable, "run", "--data-dir", str(tmp_path))


async def _reap(process: asyncio.subprocess.Process) -> None:
    """Wait for the program to end and close its pipes, so no transport is left open."""
    await asyncio.wait_for(process.communicate(), timeout=20)


async def _start(shim: LinuxShim, code: str, tmp_path: Path) -> asyncio.subprocess.Process:
    """Start ``code`` in this interpreter as a group leader and wait until it says it is ready."""
    spec = SpawnSpec(
        argv=(sys.executable, "-c", code),
        cwd=tmp_path,
        env={},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    process = await shim.spawn(spec)
    assert process.stdout is not None
    await asyncio.wait_for(process.stdout.readline(), timeout=20)
    return process


@posix_only
@pytest.mark.parametrize("graceful", [True, False])
async def test_stopping_a_group_ends_the_leader_and_its_children(
    tmp_path: Path, graceful: bool
) -> None:
    shim = LinuxShim(tmp_path, {})
    code = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = await _start(shim, code, tmp_path)
    ref = shim.process_ref(process.pid)
    assert await stop_group(shim, ref, graceful=graceful, clock=FastClock()) is True
    await _reap(process)
    assert shim.group_alive(ref) is False


@posix_only
async def test_a_group_that_ignores_the_polite_signal_is_forced(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _start(shim, TERM_PROOF, tmp_path)
    ref = shim.process_ref(process.pid)
    assert await stop_group(shim, ref, graceful=True, clock=FastClock()) is True
    await _reap(process)
    assert process.returncode == -9  # SIGKILL: the TERM was ignored.


@posix_only
async def test_stopping_a_group_that_is_gone_does_nothing(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _start(shim, "print('ready', flush=True)", tmp_path)
    await _reap(process)
    ref = ProcessRef(process.pid, 0.0)
    assert await stop_group(shim, ref, graceful=True, clock=FastClock()) is False
