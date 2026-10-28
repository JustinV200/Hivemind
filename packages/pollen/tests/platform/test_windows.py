"""Test the Windows shim: LocalAppData, taskkill trees and a Scheduled Task, on any host.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its
files in a ``pollen`` folder under LocalAppData, runs each program in a process group and a Job
Object of its own, ends a tree whose job it does not hold with ``taskkill /T``, and starts at
logon as a Scheduled Task. The shim is written
so it imports and its pure parts run on every host, which is what lets CI check it without a
Windows machine: a recorder stands in for ``schtasks`` and ``taskkill``, and the process-tree
rules are exercised against real programs on the host's own process table.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.windows.

Key invariants:
    - The Scheduled Task is created, started and removed by name; nothing else is touched.
    - A job ends everything in it, a child its leader left behind included.
    - A tree with no job here is ended with ``taskkill /T`` (forced with ``/F``), and only the pid
      the lease started.
    - A program gets a console with no window unless it shares the packet's (the Warden); an
      INTERRUPT or TERMINATE that no break event can reach still ends the program, by force.
    - The Scheduled Task runs the packet under a console host with no window, logging to a file.
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

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW, which only Windows' subprocess module defines.


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


def test_install_creates_and_starts_a_windowless_scheduled_task(tmp_path: Path) -> None:
    runner = Schtasks()
    plan = ServicePlan(("C:\\Python\\python.exe", "-m", "pollen", "run"), tmp_path)
    env = {"SYSTEMROOT": "D:\\Win"}
    result = WindowsShim(tmp_path, env, runner).install_service(plan)
    assert result.is_active and result.unit_path is None
    create, run = runner.calls
    assert create[:5] == ("schtasks", "/Create", "/TN", TASK_NAME, "/TR")
    # A console host with no window runs the packet, which logs to a file nobody has to watch.
    assert create[5] == (
        "D:\\Win\\System32\\conhost.exe --headless C:\\Python\\python.exe -m pollen run "
        "--log-to-file"
    )
    assert create[6:] == ("/SC", "ONLOGON", "/F")
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


@pytest.mark.parametrize(("shares_console", "windowless"), [(False, True), (True, False)])
async def test_a_program_gets_a_console_with_no_window_unless_it_shares_the_packets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shares_console: bool, windowless: bool
) -> None:
    flags: list[int] = []

    async def record(*_argv: str, creationflags: int, **_kwargs: object) -> object:
        flags.append(creationflags)
        return object()

    monkeypatch.setattr("pollen.platform.windows.asyncio.create_subprocess_exec", record)
    monkeypatch.setattr("pollen.platform.windows.SUSPENDED_FLAG", 0)  # No job for a fake.
    monkeypatch.setattr("pollen.platform.windows._NO_WINDOW", NO_WINDOW)  # 0 off Windows.
    spec = SpawnSpec(("cmd",), tmp_path, {}, None, None, None, shares_console=shares_console)

    await WindowsShim(tmp_path, {}, Schtasks()).spawn(spec)

    assert bool(flags[0] & NO_WINDOW) is windowless


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


async def test_a_tree_with_no_job_here_is_alive_while_its_leader_runs_and_signals_use_taskkill(
    tmp_path: Path,
) -> None:
    runner = Schtasks()
    process = await _spawn(
        WindowsShim(tmp_path, {}, Schtasks()),
        "import time; print('ready', flush=True); time.sleep(60)",
        tmp_path,
    )
    # Another shim holds no job for it, as after a packet restart or from the CLI's process.
    shim = WindowsShim(tmp_path, {}, runner)
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


_POLITE = ("taskkill", "/T", "/PID", "7")
_FORCED = ("taskkill", "/T", "/F", "/PID", "7")


@pytest.mark.parametrize(
    ("signal_", "tree", "expected"),
    [
        # tree: (attached to the packet's console, a job held here); expected: (taskkill calls,
        # break events, job kills). The Warden shares the packet's console: a break reaches it.
        (ProcessSignal.INTERRUPT, (True, True), ([], 1, 0)),
        (ProcessSignal.TERMINATE, (True, True), ([_POLITE], 1, 0)),
        # A windowless program has a console of its own: no break reaches it, so it is forced.
        (ProcessSignal.INTERRUPT, (False, True), ([_POLITE], 0, 1)),
        (ProcessSignal.TERMINATE, (False, False), ([_POLITE, _FORCED], 0, 0)),
    ],
)
def test_interrupt_and_terminate_end_a_console_program_a_break_cannot_reach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_: ProcessSignal,
    tree: tuple[bool, bool],
    expected: tuple[list[tuple[str, ...]], int, int],
) -> None:
    reaches_console, holds_job = tree
    ran: list[tuple[str, ...]] = []
    sent: list[int] = []
    killed: list[ProcessRef] = []

    def taskkill(argv: Sequence[str]) -> CommandResult:
        ran.append(tuple(argv))
        # A console program has no window for a polite taskkill to close; only /F succeeds.
        return CommandResult(0 if "/F" in argv else 1, "")

    def job_kill(ref: ProcessRef) -> bool | None:
        if not holds_job:
            return None  # No job here: the shim falls back to taskkill /F.
        killed.append(ref)
        return True

    shim = WindowsShim(tmp_path, {}, taskkill)
    monkeypatch.setattr(shim, "group_alive", lambda _ref: True)
    monkeypatch.setattr(shim._jobs, "kill", job_kill)
    monkeypatch.setattr("pollen.platform.windows._on_our_console", lambda _pid: reaches_console)
    monkeypatch.setattr("pollen.platform.windows.os.kill", lambda _pid, sig: sent.append(sig))

    assert shim.signal_group(ProcessRef(7, 1.0), signal_) is True
    assert (ran, len(sent), len(killed)) == expected


async def test_a_kill_taskkill_refuses_is_reported_and_never_softened_into_a_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = await _spawn(
        WindowsShim(tmp_path, {}, Schtasks()),
        "import time; print('ready', flush=True); time.sleep(60)",
        tmp_path,
    )
    shim = WindowsShim(tmp_path, {}, Schtasks(failing=frozenset({"taskkill"})))  # Holds no job.
    sent: list[int] = []
    try:
        with monkeypatch.context() as patch:
            patch.setattr("pollen.platform.windows.os.kill", lambda _pid, sig: sent.append(sig))
            assert shim.signal_group(shim.process_ref(process.pid), ProcessSignal.KILL) is False
        assert sent == []  # A forced stop that failed is reported, never turned into a request.
    finally:
        process.kill()
        await _reap(process)


@pytest.mark.skipif(sys.platform != "win32", reason="windowless consoles exist on Windows only")
@pytest.mark.parametrize(
    ("code", "signal_"),
    [
        # A break to a program in a console of its own reported success and did nothing.
        ("import time; print('ready', flush=True); time.sleep(60)", ProcessSignal.INTERRUPT),
        # That console's host outlived a program that ended by itself, so its job looked alive.
        ("print('ready', flush=True)", None),
    ],
)
async def test_a_windowless_program_ends_and_its_group_is_gone_once_reaped(
    tmp_path: Path, code: str, signal_: ProcessSignal | None
) -> None:
    shim = WindowsShim(tmp_path, {})  # The real taskkill, which refuses a console program.
    process = await _spawn(shim, code, tmp_path)
    ref = shim.process_ref(process.pid)
    try:
        if signal_ is not None:
            assert shim.signal_group(ref, signal_) is True
        await _reap(process)
        assert process.returncode is not None
        assert shim.group_alive(ref) is False
    finally:
        shim.signal_group(ref, ProcessSignal.KILL)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects exist on Windows only")
async def test_a_job_keeps_a_child_its_leader_left_behind_and_a_kill_ends_it(
    tmp_path: Path,
) -> None:
    runner = Schtasks()
    shim = WindowsShim(tmp_path, {}, runner)
    code = (
        "import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], stdout=subprocess.DEVNULL); print('ready', flush=True)"
    )
    process = await _spawn(shim, code, tmp_path)
    ref = shim.process_ref(process.pid)
    try:
        await _reap(process)  # The leader is gone; its child is not below anything now.
        assert shim.group_alive(ref)
        assert shim.signal_group(ref, ProcessSignal.KILL) is True
        for _ in range(100):
            if not shim.group_alive(ref):
                break
            await asyncio.sleep(0.05)
        assert shim.group_alive(ref) is False
        assert runner.calls == []  # The job ended it, not taskkill.
    finally:
        shim.signal_group(ref, ProcessSignal.KILL)
