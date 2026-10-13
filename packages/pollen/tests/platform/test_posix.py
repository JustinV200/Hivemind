"""Test the POSIX process model: groups through sessions, signals through killpg, recycled pids.

Linux and macOS share one way of running a program the way the Pollen Packet (the thin gateway
program on a device the Hive borrows) needs it: as the leader of a session and process group of
its own, recorded as a pid together with its creation time so that a pid the kernel has recycled
for something else is never signalled. The group is alive while the leader or a descendant lives
(a zombie is not alive), and a signal goes to the whole group or to nothing. These tests start real
programs through ``LinuxShim``, the concrete shim, and look at what the kernel says.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.posix through its concrete
    subclasses.

Key invariants:
    - A child spawned by the shim leads its own process group.
    - A reference with the wrong creation time names no process at all.

See Also:
    - docs/waggle/gateway-contract.md section 11 (process groups).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from pollen.platform import LinuxShim, ProcessRef, SpawnSpec
from waggle.messages.session import ProcessSignal

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process model")
SLEEPER = "import time; print('ready', flush=True); time.sleep(60)"


async def _reap(process: asyncio.subprocess.Process) -> None:
    """Wait for the program to end and close its pipes, so no transport is left open."""
    await asyncio.wait_for(process.communicate(), timeout=20)


async def _spawn(shim: LinuxShim, code: str, tmp_path: Path) -> asyncio.subprocess.Process:
    """Start ``code`` as a group leader and wait for its first line."""
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


async def test_a_spawned_program_leads_a_session_and_group_of_its_own(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, SLEEPER, tmp_path)
    try:
        assert os.getpgid(process.pid) == process.pid != os.getpgid(0)
        ref = shim.process_ref(process.pid)
        assert ref.created == pytest.approx(psutil.Process(process.pid).create_time())
        assert shim.group_alive(ref)
    finally:
        assert shim.signal_group(ref, ProcessSignal.KILL)
        await _reap(process)


@pytest.mark.parametrize(
    ("which", "code"),
    [
        (ProcessSignal.INTERRUPT, -2),
        (ProcessSignal.TERMINATE, -15),
        (ProcessSignal.KILL, -9),
    ],
)
async def test_each_signal_reaches_the_group(
    tmp_path: Path, which: ProcessSignal, code: int
) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, SLEEPER, tmp_path)
    assert shim.signal_group(shim.process_ref(process.pid), which) is True
    await _reap(process)
    # Python turns SIGINT into KeyboardInterrupt and exits 1; the others end it by the signal.
    assert process.returncode in {code, 1}


async def test_a_group_that_is_gone_cannot_be_signalled(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, "print('ready', flush=True)", tmp_path)
    ref = shim.process_ref(process.pid)
    await _reap(process)
    assert shim.group_alive(ref) is False
    assert shim.signal_group(ref, ProcessSignal.KILL) is False


async def test_a_zombie_leader_is_not_alive(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, SLEEPER, tmp_path)
    ref = shim.process_ref(process.pid)
    os.kill(process.pid, 9)  # Dead, but not yet waited for: a zombie.
    for _ in range(200):
        if not shim.group_alive(ref):
            break
        await asyncio.sleep(0.01)
    assert shim.group_alive(ref) is False
    await _reap(process)


async def test_a_recycled_pid_is_not_the_process_that_was_recorded(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, SLEEPER, tmp_path)
    try:
        impostor = ProcessRef(process.pid, shim.process_ref(process.pid).created - 1000)
        assert shim.group_alive(impostor) is False
        assert shim.signal_group(impostor, ProcessSignal.KILL) is False
        assert process.returncode is None  # Untouched.
    finally:
        shim.signal_group(shim.process_ref(process.pid), ProcessSignal.KILL)
        await _reap(process)


async def test_a_descendant_keeps_the_group_alive_after_its_leader_goes(tmp_path: Path) -> None:
    shim = LinuxShim(tmp_path, {})
    code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                 stdout=subprocess.DEVNULL)\n"  # Not holding the leader's pipe open.
        "print('ready', flush=True)\n"
    )
    process = await _spawn(shim, code, tmp_path)
    ref = shim.process_ref(process.pid)
    await _reap(process)  # The leader has exited and been reaped.
    try:
        assert shim.group_alive(ref) is True
    finally:
        shim.signal_group(ref, ProcessSignal.KILL)
        for _ in range(500):
            if not shim.group_alive(ref):
                break
            await asyncio.sleep(0.01)


def test_the_process_reference_of_a_missing_pid_has_no_creation_time(tmp_path: Path) -> None:
    assert LinuxShim(tmp_path, {}).process_ref(2**22 + 77) == ProcessRef(2**22 + 77, 0.0)


def test_the_facts_follow_the_environment(tmp_path: Path) -> None:
    with_display = LinuxShim(tmp_path, {"DISPLAY": ":0", "SHELL": "/bin/zsh"}).facts()
    without = LinuxShim(tmp_path, {}).facts()
    assert (with_display.has_display, with_display.shell) == (True, "/bin/zsh")
    assert (without.has_display, without.shell) == (False, "/bin/sh")
    assert without.architecture and with_display.os_family.value == "LINUX"


async def test_a_group_that_exits_between_the_look_and_the_signal_reports_nothing_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, SLEEPER, tmp_path)
    ref = shim.process_ref(process.pid)

    probe = os.killpg

    def too_late(group: int, number: int) -> None:
        if number == 0:
            probe(group, number)  # The look finds the group alive...
        else:
            raise ProcessLookupError  # ...and it is gone by the time the signal is sent.

    try:
        with monkeypatch.context() as patch:
            patch.setattr("pollen.platform.posix.os.killpg", too_late)
            assert shim.signal_group(ref, ProcessSignal.KILL) is False
    finally:
        process.kill()
        await _reap(process)


async def test_a_process_that_vanishes_while_the_group_is_scanned_is_not_a_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = LinuxShim(tmp_path, {})
    process = await _spawn(shim, SLEEPER, tmp_path)
    ref = shim.process_ref(process.pid)

    def gone(_pid: int) -> int:
        raise ProcessLookupError

    try:
        with monkeypatch.context() as patch:
            patch.setattr("pollen.platform.posix.os.getpgid", gone)
            assert shim.group_alive(ref) is False
    finally:
        process.kill()
        await _reap(process)
