"""Test the Windows Job Object table and the shim's use of it, against a fake kernel32.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) puts each
child it starts in a Job Object, kills the tree through the job and asks the job whether any
process of the tree is left, so a detached grandchild neither escapes a release nor hides from
``group_alive`` (gateway-contract section 3). No host here runs Windows, so the kernel32 slice the
table uses is a fake that keeps jobs in a dictionary and records every call: the tests check the
calls the table makes (the information classes, the limit flag, the handles opened and closed) and
the answers the shim gives from them. The structures' layouts are checked against the sizes the
SDK documents. What stays unproven without a Windows host is the kernel's side: that these calls,
so made, do what the documentation says.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.windows.jobs and the job
    paths of pollen.platform.windows.

Key invariants:
    - Every handle the table opens is closed: a process handle right after the assignment, a job
      handle when the job is empty or when a step of its creation was refused.
    - A refused job leaves the table holding nothing, so the shim keeps to taskkill.
    - A job's answer, not the process table's, decides whether a tree lives or is killed.

See Also:
    - test_windows.py for the taskkill and process-table paths the job falls back to.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import subprocess
import sys
from collections.abc import Sequence
from ctypes import _Pointer
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pollen.platform import CommandResult, ProcessRef, SpawnSpec, WindowsShim
from pollen.platform.windows.jobs import (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    BasicAccounting,
    ExtendedLimits,
    JobObjects,
    host_kernel32,
)
from waggle.messages.session import ProcessSignal

PROCESS_SET_QUOTA_AND_TERMINATE = 0x0101  # The access the assignment needs, from the SDK.


@dataclass
class FakeJob:
    """One job the fake kernel holds: its limit flags, its members and its live count."""

    flags: int = 0
    pids: list[int] = field(default_factory=list)
    active: int = 0  # What the accounting answers; a test sets it to stage the tree's life.
    terminated: bool = False


class FakeKernel32:
    """The kernel32 slice as a dictionary of jobs, refusing the steps a test names."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        """Refuse the calls named in ``refuse`` (by their Win32 name); answer the rest."""
        self.refuse = refuse
        self.jobs: dict[int, FakeJob] = {}
        self.open_handles: set[int] = set()
        self.calls: list[tuple[str, ...]] = []
        self._next = 0x100

    def _handle(self) -> int:
        """Hand out a fresh handle and remember it is open."""
        self._next += 4
        self.open_handles.add(self._next)
        return self._next

    def CreateJobObjectW(self, attributes: None, name: None) -> int | None:  # noqa: N802  # The Win32 name.
        """Create an anonymous job."""
        self.calls.append(("CreateJobObjectW", str(attributes), str(name)))
        if "CreateJobObjectW" in self.refuse:
            return None
        handle = self._handle()
        self.jobs[handle] = FakeJob()
        return handle

    def SetInformationJobObject(  # noqa: N802  # The Win32 name.
        self, job: int, info_class: int, info: _Pointer[ExtendedLimits], size: int
    ) -> int:
        """Record the limit flags set on the job."""
        self.calls.append(("SetInformationJobObject", str(info_class), str(size)))
        if "SetInformationJobObject" in self.refuse:
            return 0
        assert size == ctypes.sizeof(ExtendedLimits) and info_class == 9
        self.jobs[job].flags = info.contents.limit_flags()
        return 1

    def AssignProcessToJobObject(self, job: int, process: int) -> int:  # noqa: N802  # The Win32 name.
        """Put the process behind ``process`` in the job."""
        self.calls.append(("AssignProcessToJobObject", str(job), str(process)))
        assert process in self.open_handles  # A closed handle is a bug, whatever the answer.
        if "AssignProcessToJobObject" in self.refuse:
            return 0
        self.jobs[job].pids.append(process - 1)  # OpenProcess hands out pid + 1 below.
        self.jobs[job].active += 1
        return 1

    def TerminateJobObject(self, job: int, exit_code: int) -> int:  # noqa: N802  # The Win32 name.
        """End the job's processes."""
        self.calls.append(("TerminateJobObject", str(job), str(exit_code)))
        if "TerminateJobObject" in self.refuse:
            return 0
        self.jobs[job].terminated = True
        self.jobs[job].active = 0
        return 1

    def QueryInformationJobObject(  # noqa: N802  # The Win32 name.
        self, job: int, info_class: int, info: _Pointer[BasicAccounting], size: int, returned: None
    ) -> int:
        """Answer the job's live process count."""
        self.calls.append(("QueryInformationJobObject", str(info_class), str(size)))
        if "QueryInformationJobObject" in self.refuse:
            return 0
        assert size == ctypes.sizeof(BasicAccounting) and info_class == 1 and returned is None
        info.contents.ActiveProcesses = self.jobs[job].active
        return 1

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int | None:  # noqa: N802  # The Win32 name.
        """Open a handle on ``pid``: its value is pid + 1, so a test can read it back."""
        self.calls.append(("OpenProcess", str(access), str(inherit), str(pid)))
        if "OpenProcess" in self.refuse:
            return None
        assert access == PROCESS_SET_QUOTA_AND_TERMINATE and inherit is False
        self.open_handles.add(pid + 1)
        return pid + 1

    def CloseHandle(self, handle: int) -> int:  # noqa: N802  # The Win32 name.
        """Close a handle that is open."""
        self.calls.append(("CloseHandle", str(handle)))
        assert handle in self.open_handles  # Closing twice, or a stranger, is a bug.
        self.open_handles.remove(handle)
        return 1

    def only_job(self) -> FakeJob:
        """Return the one job created, for a test that made exactly one."""
        (job,) = self.jobs.values()
        return job


def _ref(pid: int = 4242, created: float = 1_700_000_000.0) -> ProcessRef:
    """A child the lease started, as the shim records it."""
    return ProcessRef(pid, created)


def _names(kernel: FakeKernel32) -> list[str]:
    """The Win32 calls made so far, in order."""
    return [call[0] for call in kernel.calls]


# ──────────────────────────────────────────────────────────────────────────────
# The structures
# ──────────────────────────────────────────────────────────────────────────────


def test_the_structures_have_the_sizes_the_sdk_documents() -> None:
    # 144 and 48 on a 64-bit build (SIZE_T and ULONG_PTR are 8 bytes), 112 and 48 on a 32-bit one.
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(ExtendedLimits) == (144 if pointer == 8 else 112)
    assert ctypes.sizeof(BasicAccounting) == 48


def test_kernel32_is_bound_on_windows_only() -> None:
    assert (host_kernel32() is not None) == (sys.platform == "win32")


# ──────────────────────────────────────────────────────────────────────────────
# Enrolling a child
# ──────────────────────────────────────────────────────────────────────────────


def test_a_child_is_put_in_a_job_that_dies_with_the_packet() -> None:
    kernel = FakeKernel32()
    assert JobObjects(kernel).enroll(_ref(), outlives_packet=False) is True
    job = kernel.only_job()
    assert job.flags == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and job.pids == [4242]
    assert _names(kernel) == [
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CloseHandle",  # The process handle; the job's stays open while the tree may live.
    ]
    assert kernel.open_handles == set(kernel.jobs)


def test_a_child_that_outlives_the_packet_gets_no_kill_on_close_limit() -> None:
    kernel = FakeKernel32()
    assert JobObjects(kernel).enroll(_ref(), outlives_packet=True) is True
    assert "SetInformationJobObject" not in _names(kernel)
    assert kernel.only_job().flags == 0 and kernel.only_job().pids == [4242]


@pytest.mark.parametrize(
    "refused",
    ["CreateJobObjectW", "SetInformationJobObject", "OpenProcess", "AssignProcessToJobObject"],
)
def test_a_refused_step_leaves_no_job_and_no_open_handle(
    refused: str, caplog: pytest.LogCaptureFixture
) -> None:
    kernel = FakeKernel32(refuse=frozenset({refused}))
    table = JobObjects(kernel)
    with caplog.at_level(logging.DEBUG, logger="pollen.platform.windows.jobs"):
        assert table.enroll(_ref(), outlives_packet=False) is False
        assert table.enroll(_ref(pid=4343), outlives_packet=False) is False
    assert kernel.open_handles == set()  # Every job and process handle opened was closed again.
    assert table.alive(_ref()) is None and table.terminate(_ref()) is None
    # The first refusal is for the operator, the next for a debug log: a host that refuses
    # jobs refuses them for every child, and one warning says so.
    levels = [r.levelno for r in caplog.records if "job_object_refused" in r.getMessage()]
    assert levels == [logging.WARNING, logging.DEBUG]


def test_without_a_kernel32_the_table_holds_nothing_and_answers_nothing() -> None:
    table = JobObjects(None)
    assert table.enroll(_ref(), outlives_packet=False) is False
    assert table.alive(_ref()) is None and table.terminate(_ref()) is None


# ──────────────────────────────────────────────────────────────────────────────
# Asking and ending a job
# ──────────────────────────────────────────────────────────────────────────────


def test_a_job_is_alive_while_it_holds_a_process_and_is_closed_once_empty() -> None:
    kernel = FakeKernel32()
    table = JobObjects(kernel)
    table.enroll(_ref(), outlives_packet=False)
    assert table.alive(_ref()) is True
    kernel.only_job().active = 0  # The leader and every descendant have ended.
    assert table.alive(_ref()) is False
    assert kernel.open_handles == set()  # The job handle went with the last process.
    assert table.alive(_ref()) is None  # Forgotten: the process table decides from here.


def test_a_job_that_cannot_be_asked_is_let_go() -> None:
    kernel = FakeKernel32(refuse=frozenset({"QueryInformationJobObject"}))
    table = JobObjects(kernel)
    table.enroll(_ref(), outlives_packet=False)
    assert table.alive(_ref()) is None and kernel.open_handles == set()


def test_terminating_ends_the_whole_job_and_reports_a_refusal() -> None:
    kernel = FakeKernel32()
    table = JobObjects(kernel)
    table.enroll(_ref(), outlives_packet=False)
    assert table.terminate(_ref()) is True and kernel.only_job().terminated
    assert table.alive(_ref()) is False  # Nothing left in it.
    refusing = FakeKernel32(refuse=frozenset({"TerminateJobObject"}))
    stubborn = JobObjects(refusing)
    stubborn.enroll(_ref(), outlives_packet=False)
    assert stubborn.terminate(_ref()) is False  # The shim then falls back to taskkill.


def test_a_recorded_pid_from_another_run_does_not_match_this_runs_job() -> None:
    table = JobObjects(FakeKernel32())
    table.enroll(_ref(), outlives_packet=False)
    earlier = _ref(created=1_700_000_000.0 - 3600)  # The same pid, an hour before.
    assert table.alive(earlier) is None and table.terminate(earlier) is None
    assert table.alive(_ref(created=0.0)) is True  # An unknown time cannot be checked.


# ──────────────────────────────────────────────────────────────────────────────
# The shim over the table
# ──────────────────────────────────────────────────────────────────────────────


class Taskkill:
    """A runner that records every command and answers it with success."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        """Record the command."""
        self.calls.append(tuple(argv))
        return CommandResult(0, "")


async def _spawn(
    shim: WindowsShim, tmp_path: Path, *, outlives_packet: bool = False
) -> asyncio.subprocess.Process:
    """Start a sleeping program through the shim and wait for its first line."""
    spec = SpawnSpec(
        argv=(sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"),
        cwd=tmp_path,
        env={},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        outlives_packet=outlives_packet,
    )
    process = await shim.spawn(spec)
    assert process.stdout is not None
    await asyncio.wait_for(process.stdout.readline(), timeout=20)
    return process


async def _reap(process: asyncio.subprocess.Process) -> None:
    """End the program and close its pipes, so no transport is left open."""
    process.kill()
    await asyncio.wait_for(process.communicate(), timeout=20)


async def test_a_spawned_child_is_enrolled_in_a_job_with_its_own_pid(tmp_path: Path) -> None:
    kernel = FakeKernel32()
    shim = WindowsShim(tmp_path, {}, Taskkill(), JobObjects(kernel))
    process = await _spawn(shim, tmp_path)
    try:
        assert kernel.only_job().pids == [process.pid]
        assert kernel.only_job().flags == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    finally:
        await _reap(process)


async def test_the_warden_is_spawned_into_a_job_it_can_outlive_the_packet_in(
    tmp_path: Path,
) -> None:
    kernel = FakeKernel32()
    shim = WindowsShim(tmp_path, {}, Taskkill(), JobObjects(kernel))
    process = await _spawn(shim, tmp_path, outlives_packet=True)
    try:
        assert kernel.only_job().pids == [process.pid] and kernel.only_job().flags == 0
    finally:
        await _reap(process)


async def test_a_tree_is_alive_while_its_job_holds_a_process_the_leader_left_behind(
    tmp_path: Path,
) -> None:
    kernel = FakeKernel32()
    shim = WindowsShim(tmp_path, {}, Taskkill(), JobObjects(kernel))
    process = await _spawn(shim, tmp_path)
    ref = shim.process_ref(process.pid)
    await _reap(process)  # The leader is gone from the process table...
    kernel.only_job().active = 1  # ...but a detached grandchild of it is still in the job.
    assert shim.group_alive(ref) is True
    kernel.only_job().active = 0
    assert shim.group_alive(ref) is False


async def test_a_kill_goes_through_the_job_and_never_through_taskkill(tmp_path: Path) -> None:
    kernel = FakeKernel32()
    runner = Taskkill()
    shim = WindowsShim(tmp_path, {}, runner, JobObjects(kernel))
    process = await _spawn(shim, tmp_path)
    try:
        ref = shim.process_ref(process.pid)
        assert shim.signal_group(ref, ProcessSignal.KILL) is True
        assert kernel.only_job().terminated and runner.calls == []
        # The polite stop still asks through taskkill: a job has no polite way of asking.
        kernel.only_job().active = 1
        shim.signal_group(ref, ProcessSignal.TERMINATE)
        assert runner.calls == [("taskkill", "/T", "/PID", str(process.pid))]
    finally:
        await _reap(process)


async def test_a_kill_the_job_refuses_falls_back_to_taskkill(tmp_path: Path) -> None:
    kernel = FakeKernel32(refuse=frozenset({"TerminateJobObject"}))
    runner = Taskkill()
    shim = WindowsShim(tmp_path, {}, runner, JobObjects(kernel))
    process = await _spawn(shim, tmp_path)
    try:
        assert shim.signal_group(shim.process_ref(process.pid), ProcessSignal.KILL) is True
        assert runner.calls == [("taskkill", "/T", "/F", "/PID", str(process.pid))]
    finally:
        await _reap(process)


async def test_a_child_no_job_could_be_made_for_is_handled_by_the_process_table(
    tmp_path: Path,
) -> None:
    kernel = FakeKernel32(refuse=frozenset({"AssignProcessToJobObject"}))
    runner = Taskkill()
    shim = WindowsShim(tmp_path, {}, runner, JobObjects(kernel))
    process = await _spawn(shim, tmp_path)
    ref = shim.process_ref(process.pid)
    try:
        assert shim.group_alive(ref) is True  # psutil sees the leader.
        assert shim.signal_group(ref, ProcessSignal.KILL) is True
        assert runner.calls == [("taskkill", "/T", "/F", "/PID", str(process.pid))]
    finally:
        await _reap(process)
    assert shim.group_alive(ref) is False
