"""Test the platform seam's shared pieces: running a service command and stopping a process group.

The Pollen Packet (the thin gateway program on a device the Hive borrows) differs from host to
host in only a few ways, and the rest of pollen holds a ``PlatformShim`` instead of looking at the
operating system. The pieces every shim shares live in ``pollen.platform.base``: ``run_command``
runs one service-manager command as an argument list, never through a shell, and reports a program
that is missing as a status rather than an exception; ``launch_command`` is the argv a service
unit runs; ``stop_group`` ends a whole process group, politely first when asked and by force
after a grace that the caller may lengthen (a lease's program gets a short one, the on-device
Warden, which has a lease to give back, a long one), so a program's children never outlive it;
``own_interpreter`` offers the running interpreter as one to build a runtime with;
``child_environment`` is the environment every program the packet starts inherits, which in a
single-file build must not carry the loader path of the libraries the build unpacked for itself.
Most of these tests run real programs; the length of the grace is checked against a group that
never leaves.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.base.

Key invariants:
    - A command that cannot run is a status of 127, never an exception.
    - A program started by a frozen packet sees the device's own loader path, never the packet's.
    - A stopped group has no live member afterwards, even one that ignored the polite signal.
    - A graceful stop waits the grace it is given and not longer than a group takes to leave.

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
from pollen.platform.base import (
    COMMAND_UNAVAILABLE,
    KILL_GRACE_S,
    child_environment,
    own_interpreter,
    run_command,
    stop_group,
)
from waggle.messages.session import ProcessSignal

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


def test_a_service_command_in_a_frozen_build_runs_with_the_devices_loader_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unpacked/_MEI123")  # What the bootloader set,
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")  # and what it was before.
    code = "import os; print(os.environ.get('LD_LIBRARY_PATH'))"

    assert run_command([sys.executable, "-c", code]).output == "/opt/lib"


def test_a_child_does_not_inherit_the_variables_that_point_at_the_packets_libraries() -> None:
    base = {"PATH": "/usr/bin", "PYTHONPATH": "/x", "PYTHONHOME": "/y", "VIRTUAL_ENV": "/v"}

    assert child_environment(base) == {"PATH": "/usr/bin"}


def test_a_frozen_builds_loader_paths_are_put_back_for_a_system_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    base = {
        "LD_LIBRARY_PATH": "/unpacked/_MEI123",  # What the bootloader set,
        "LD_LIBRARY_PATH_ORIG": "/opt/lib",  # and what it was before.
        "DYLD_LIBRARY_PATH": "/unpacked/_MEI123",  # Set with nothing before it: to be removed.
        "HOME": "/home/me",
    }

    env = child_environment(base)

    # A single-file program started below the packet, the packet itself among them, must not take
    # itself for a worker of this instance: with the loader path gone it could not import.
    assert env == {
        "LD_LIBRARY_PATH": "/opt/lib",
        "HOME": "/home/me",
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
    }


def test_an_ordinary_install_keeps_its_loader_paths_and_adds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert child_environment({"LD_LIBRARY_PATH": "/opt/lib"}) == {"LD_LIBRARY_PATH": "/opt/lib"}


def test_a_command_that_hangs_is_reported_after_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pollen.platform.base.COMMAND_TIMEOUT_S", 0.2)
    assert run_command([sys.executable, "-c", "import time; time.sleep(30)"]).returncode == 127


def test_the_launch_command_runs_this_interpreter_as_a_module(tmp_path: Path) -> None:
    command = launch_command(tmp_path)
    assert command == (sys.executable, "-m", "pollen", "run", "--data-dir", str(tmp_path))


def test_a_frozen_build_launches_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert launch_command(tmp_path) == (sys.executable, "run", "--data-dir", str(tmp_path))


def test_this_interpreter_is_offered_to_build_a_runtime_unless_the_build_is_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert own_interpreter() == ((sys.executable,),)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert own_interpreter() == ()  # A frozen packet's executable is the packet, not Python.


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


class _StubbornShim(LinuxShim):
    """A shim over a group nobody can see, which stays until it is killed (or asked, if it may)."""

    def __init__(self, home: Path, *, leaves_on_term: bool = False) -> None:
        """Choose whether the polite signal is enough to end the group."""
        super().__init__(home, {})
        self.signals: list[ProcessSignal] = []
        self._leaves_on_term = leaves_on_term
        self._gone = False

    def group_alive(self, ref: ProcessRef) -> bool:
        """Say that the group is there until a signal that ends it was sent."""
        return not self._gone

    def signal_group(self, ref: ProcessRef, signal_: ProcessSignal) -> bool:
        """Record the signal; KILL, and TERMINATE when the group is polite, ends the group."""
        self.signals.append(signal_)
        ends = signal_ is ProcessSignal.KILL or (
            self._leaves_on_term and signal_ is ProcessSignal.TERMINATE
        )
        self._gone = self._gone or ends
        return True


class _CountingClock(FastClock):
    """A clock that does not wait and adds up the time it was asked to wait."""

    def __init__(self) -> None:
        """Start with nothing waited."""
        super().__init__(max_sleep_s=0.0)
        self.waited_s = 0.0

    async def sleep(self, seconds: float) -> None:
        """Count ``seconds`` and only yield."""
        self.waited_s += seconds
        await super().sleep(seconds)


async def test_a_graceful_stop_waits_the_grace_it_is_given_before_it_forces(
    tmp_path: Path,
) -> None:
    ref = ProcessRef(4321, 1.0)
    brief, long = _StubbornShim(tmp_path), _StubbornShim(tmp_path)
    brief_clock, long_clock = _CountingClock(), _CountingClock()

    assert await stop_group(brief, ref, graceful=True, clock=brief_clock) is True
    assert await stop_group(long, ref, graceful=True, clock=long_clock, grace_s=3.0) is True

    both = [ProcessSignal.TERMINATE, ProcessSignal.KILL]  # Asked, then forced.
    assert brief.signals == long.signals == both
    assert brief_clock.waited_s == pytest.approx(KILL_GRACE_S, abs=0.1)
    assert long_clock.waited_s == pytest.approx(3.0, abs=0.1)


async def test_a_graceful_stop_does_not_wait_out_the_grace_for_a_group_that_leaves(
    tmp_path: Path,
) -> None:
    shim = _StubbornShim(tmp_path, leaves_on_term=True)
    clock = _CountingClock()

    assert await stop_group(shim, ProcessRef(4321, 1.0), graceful=True, clock=clock, grace_s=30.0)

    assert clock.waited_s == 0.0
    # Forced even so: a child the leader started may have outlived it.
    assert shim.signals == [ProcessSignal.TERMINATE, ProcessSignal.KILL]
