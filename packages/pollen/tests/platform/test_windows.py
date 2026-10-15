"""Test the Windows shim: LocalAppData, taskkill trees and a Scheduled Task, on any host.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its
files in a ``pollen`` folder under LocalAppData, runs each program in a process group of its own
and ends a tree with ``taskkill /T``, and starts at logon as a Scheduled Task. The shim is written
so it imports and its pure parts run on every host, which is what lets CI check it without a
Windows machine: a recorder stands in for ``schtasks`` and ``taskkill``, and the process-tree
rules are exercised against real programs on the host's own process table.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.windows.

Key invariants:
    - The Scheduled Task is created, started and removed by name; nothing else is touched.
    - A tree is ended with ``taskkill /T`` (forced with ``/F``), and only the pid the lease started.
    - A virtual environment's programs are ``.exe`` files under ``Scripts``.

See Also:
    - docs/waggle/gateway-contract.md section 3.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from pollen.platform import CommandResult, ProcessRef, ServicePlan, SpawnSpec, WindowsShim
from pollen.platform.windows import TASK_NAME
from waggle.messages.session import ProcessSignal


class Schtasks:
    """A recorder that answers each command with the status scripted for its first word."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        """Fail the commands whose program or ``/Create``-style verb is in ``failing``."""
        self.failing = failing
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        """Record the command and answer it."""
        self.calls.append(tuple(argv))
        return CommandResult(1 if argv[0] in self.failing or argv[1] in self.failing else 0, "")


def test_the_data_directory_is_under_local_app_data(tmp_path: Path) -> None:
    configured = WindowsShim(tmp_path, {"LOCALAPPDATA": str(tmp_path / "local")}, Schtasks())
    assert configured.default_data_dir() == tmp_path / "local" / "pollen"
    fallback = WindowsShim(tmp_path, {}, Schtasks())
    assert fallback.default_data_dir() == tmp_path / "AppData" / "Local" / "pollen"


def test_a_windows_host_offers_the_launcher_for_the_newest_python_and_then_this_one(
    tmp_path: Path,
) -> None:
    candidates = WindowsShim(tmp_path, {}, Schtasks()).python_candidates()

    assert candidates[:3] == (("py", "-3.13"), ("py", "-3.12"), ("python",))
    assert candidates[-1] == (sys.executable,)


def test_a_virtual_environment_keeps_its_programs_in_scripts(tmp_path: Path) -> None:
    shim = WindowsShim(tmp_path, {}, Schtasks())

    assert shim.venv_executable(tmp_path / "venv", "python") == (
        tmp_path / "venv" / "Scripts" / "python.exe"
    )
    assert shim.venv_executable(tmp_path / "venv", "hivemind-in-cell").name == (
        "hivemind-in-cell.exe"
    )


def test_install_creates_and_starts_the_scheduled_task(tmp_path: Path) -> None:
    runner = Schtasks()
    plan = ServicePlan(("C:\\Python\\python.exe", "-m", "pollen", "run"), tmp_path)
    result = WindowsShim(tmp_path, {}, runner).install_service(plan)
    assert result.is_active and result.unit_path is None
    create, run = runner.calls
    assert create[:5] == ("schtasks", "/Create", "/TN", TASK_NAME, "/TR")
    assert "-m pollen run" in create[5] and create[6:] == ("/SC", "ONLOGON", "/F")
    assert run == ("schtasks", "/Run", "/TN", TASK_NAME)


def test_install_that_schtasks_refuses_prints_the_command(tmp_path: Path) -> None:
    plan = ServicePlan(("python.exe", "-m", "pollen", "run"), tmp_path)
    result = WindowsShim(tmp_path, {}, Schtasks(frozenset({"/Create"}))).install_service(plan)
    assert not result.is_active and result.manual_command == "python.exe -m pollen run"


def test_uninstall_ends_and_deletes_the_task_and_reports_when_there_was_none(
    tmp_path: Path,
) -> None:
    runner = Schtasks()
    removed = WindowsShim(tmp_path, {}, runner).uninstall_service()
    assert runner.calls == [
        ("schtasks", "/End", "/TN", TASK_NAME),
        ("schtasks", "/Delete", "/TN", TASK_NAME, "/F"),
    ]
    assert "removed" in removed.detail
    absent = WindowsShim(tmp_path, {}, Schtasks(frozenset({"/Delete"}))).uninstall_service()
    assert "no Scheduled Task" in absent.detail


def test_the_facts_describe_a_windows_desktop(tmp_path: Path) -> None:
    facts = WindowsShim(tmp_path, {"COMSPEC": "C:\\Windows\\cmd.exe"}, Schtasks()).facts()
    assert facts.os_family.value == "WINDOWS" and facts.distribution.startswith("Windows ")  # type: ignore[union-attr]
    assert (facts.has_display, facts.has_browser, facts.shell) == (
        True,
        True,
        "C:\\Windows\\cmd.exe",
    )


async def _reap(process: asyncio.subprocess.Process) -> None:
    """Wait for the program to end and close its pipes, so no transport is left open."""
    await asyncio.wait_for(process.communicate(), timeout=20)


async def _spawn(shim: WindowsShim, code: str, tmp_path: Path) -> asyncio.subprocess.Process:
    """Start ``code`` through the Windows shim and wait for its first line."""
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


async def test_a_tree_is_alive_while_its_leader_runs_and_signals_use_taskkill(
    tmp_path: Path,
) -> None:
    runner = Schtasks()
    shim = WindowsShim(tmp_path, {}, runner)
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )
    ref = shim.process_ref(process.pid)
    try:
        assert shim.group_alive(ref)
        assert shim.signal_group(ref, ProcessSignal.TERMINATE) is True
        assert shim.signal_group(ref, ProcessSignal.KILL) is True
        assert runner.calls[0] == ("taskkill", "/T", "/PID", str(process.pid))
        assert runner.calls[1] == ("taskkill", "/T", "/F", "/PID", str(process.pid))
    finally:
        process.kill()
        await _reap(process)
    assert shim.group_alive(ref) is False
    assert shim.signal_group(ref, ProcessSignal.KILL) is False


async def test_a_recycled_pid_is_not_alive_and_an_unknown_one_has_no_creation_time(
    tmp_path: Path,
) -> None:
    shim = WindowsShim(tmp_path, {}, Schtasks())
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )
    try:
        impostor = ProcessRef(process.pid, shim.process_ref(process.pid).created - 1000)
        assert shim.group_alive(impostor) is False
    finally:
        process.kill()
        await _reap(process)
    assert shim.process_ref(2**22 + 5) == ProcessRef(2**22 + 5, 0.0)


@pytest.mark.skipif(sys.platform == "win32", reason="the break event is a signal on this host")
async def test_an_interrupt_is_delivered_as_the_hosts_break_event(tmp_path: Path) -> None:
    shim = WindowsShim(tmp_path, {}, Schtasks())
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )
    assert shim.signal_group(shim.process_ref(process.pid), ProcessSignal.INTERRUPT) is True
    await _reap(process)
    assert process.returncode != 0


@pytest.mark.skipif(sys.platform == "win32", reason="the break event is a signal on this host")
async def test_an_interrupt_with_nobody_to_receive_it_reports_nothing_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = WindowsShim(tmp_path, {}, Schtasks())
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )

    def refuse(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    try:
        with monkeypatch.context() as patch:
            patch.setattr("pollen.platform.windows.os.kill", refuse)
            assert (
                shim.signal_group(shim.process_ref(process.pid), ProcessSignal.INTERRUPT) is False
            )
    finally:
        process.kill()
        await _reap(process)


@pytest.mark.skipif(sys.platform == "win32", reason="the break event is a signal on this host")
async def test_a_terminate_taskkill_cannot_end_asks_with_the_break_event(tmp_path: Path) -> None:
    # A console program has no window for a polite taskkill to close, so taskkill refuses it.
    runner = Schtasks(failing=frozenset({"taskkill"}))
    shim = WindowsShim(tmp_path, {}, runner)
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )

    assert shim.signal_group(shim.process_ref(process.pid), ProcessSignal.TERMINATE) is True

    await _reap(process)
    assert runner.calls == [("taskkill", "/T", "/PID", str(process.pid))]
    assert process.returncode != 0


@pytest.mark.skipif(sys.platform == "win32", reason="the break event is a signal on this host")
async def test_a_terminate_neither_taskkill_nor_a_break_can_deliver_reports_nothing_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = WindowsShim(tmp_path, {}, Schtasks(failing=frozenset({"taskkill"})))
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )

    def refuse(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    try:
        with monkeypatch.context() as patch:
            patch.setattr("pollen.platform.windows.os.kill", refuse)
            ref = shim.process_ref(process.pid)
            assert shim.signal_group(ref, ProcessSignal.TERMINATE) is False
    finally:
        process.kill()
        await _reap(process)


async def test_a_kill_taskkill_refuses_is_reported_and_never_softened_into_a_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = WindowsShim(tmp_path, {}, Schtasks(failing=frozenset({"taskkill"})))
    process = await _spawn(
        shim, "import time; print('ready', flush=True); time.sleep(60)", tmp_path
    )
    sent: list[int] = []
    try:
        with monkeypatch.context() as patch:
            patch.setattr("pollen.platform.windows.os.kill", lambda _pid, sig: sent.append(sig))
            assert shim.signal_group(shim.process_ref(process.pid), ProcessSignal.KILL) is False
        assert sent == []  # A forced stop that failed is reported, never turned into a request.
    finally:
        process.kill()
        await _reap(process)
