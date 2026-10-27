"""Keep each program the Windows shim starts in a Job Object of its own, keyed by its ProcessRef.

Windows has no process groups a whole tree can be signalled through: a child whose parent has
exited is no longer below anything, so following an exec's tree through its leader loses a child
that outlived its command. A Job Object holds every process started inside it, at any depth, until
that process ends, so it answers "is anything this exec started still running" and ends all of it
at once (gateway-contract section 3). The shim starts a program suspended, puts it in a new job
here and only then lets it run, so nothing it starts can escape before it is contained. The calls
are plain kernel32 through ctypes, because the packet carries no Windows-only dependency.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.platform.windows only; calls into pollen.platform.base, psutil and the standard
    library only.

Key invariants:
    - A job is only ever found by the ProcessRef (pid plus creation time) of the program it was
      made for, so a recycled pid never reaches another exec's job.
    - A job's handle is closed once it holds no running process; a job that is not in the table
      (the packet restarted, or another process asks) is reported as unknown, never as empty.
    - Nothing here builds a command or reads request text.

See Also:
    - docs/waggle/gateway-contract.md sections 3 and 11.
    - pollen.platform.windows, the shim that owns the table.
"""

from __future__ import annotations

import asyncio
import ctypes
import functools
import sys
from dataclasses import dataclass

import psutil

from pollen.platform.base import ProcessRef

# CreateProcess's flag that leaves the new process's first thread stopped until it is resumed.
SUSPENDED_FLAG = 0x00000004 if sys.platform == "win32" else 0
_PROCESS_SET_QUOTA = 0x0100  # Access AssignProcessToJobObject needs on the process handle.
_PROCESS_TERMINATE = 0x0001  # ...and this one, too, per its documentation.
_BASIC_ACCOUNTING = 1  # JobObjectBasicAccountingInformation, the class that counts live members.
_KILLED_EXIT_CODE = 1  # What every process a job ends exits with, as taskkill /F gives it.
_CREATE_TIME_TOLERANCE_S = 1.0  # As the shims': psutil's creation time is stable per process.

__all__ = ["SUSPENDED_FLAG", "JobTable"]


class _Accounting(ctypes.Structure):
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION; only ActiveProcesses is read."""

    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_ulong),
        ("TotalProcesses", ctypes.c_ulong),
        ("ActiveProcesses", ctypes.c_ulong),  # Members that have not ended yet.
        ("TotalTerminatedProcesses", ctypes.c_ulong),
    ]


@dataclass(slots=True)
class _Job:
    """One program's job: the ref it was made for and the handle the packet holds."""

    ref: ProcessRef
    handle: int


class JobTable:
    """The Job Object of every program this shim started and that may still be running."""

    def __init__(self) -> None:
        """Start with no jobs; the table lives as long as the shim, in this process only."""
        self._jobs: list[_Job] = []

    async def contain(self, process: asyncio.subprocess.Process) -> None:
        """Put the suspended ``process`` in a new job, then let it run.

        Args:
            process: A program just started with SUSPENDED_FLAG, so it has started nothing yet.

        Raises:
            OSError: The job could not be made or the program could not be placed in it or
                resumed; the program has been killed and reaped.
        """
        try:
            self._place(process.pid)
        except OSError:
            # A program that cannot be contained never runs: it would be one no kill reaches.
            process.kill()
            await process.wait()
            raise

    def _place(self, pid: int) -> None:
        """Make ``pid``'s job, record it and resume ``pid``; OSError (nothing kept) if any fails."""
        handle = _new_job_holding(pid)
        try:
            process = psutil.Process(pid)
            ref = ProcessRef(pid, process.create_time())
            # Resumed only once inside the job: anything it starts from now on is a member too.
            process.resume()
        except psutil.Error as error:
            _close(handle)
            raise OSError(f"could not start process {pid} inside its Job Object") from error
        self._jobs.append(_Job(ref, handle))

    def alive(self, ref: ProcessRef) -> bool | None:
        """Say whether anything in ``ref``'s job still runs; None if the table has no such job.

        Args:
            ref: A process the lease started.

        Returns:
            True while any member runs; False once none does (the handle is then closed); None
            when this table never held the job, so the caller judges by other means.
        """
        job = self._find(ref)
        if job is None:
            return None
        if _active_processes(job.handle) > 0:
            return True
        # Empty for good: no process can join a job whose members have all ended.
        self._jobs.remove(job)
        _close(job.handle)
        return False

    def kill(self, ref: ProcessRef) -> bool | None:
        """End every process in ``ref``'s job; None if the table has no such job.

        Args:
            ref: A process the lease started.

        Returns:
            Whether the kernel accepted the termination; None when the job is unknown here.
        """
        job = self._find(ref)
        if job is None:
            return None
        # The handle stays open: alive() closes it once the members have actually gone.
        return _terminate(job.handle)

    def _find(self, ref: ProcessRef) -> _Job | None:
        """Return the job made for ``ref``'s pid and creation time, if this table holds one."""
        for job in self._jobs:
            if job.ref.pid == ref.pid and (
                ref.created == 0.0 or abs(job.ref.created - ref.created) <= _CREATE_TIME_TOLERANCE_S
            ):
                return job
        return None


@functools.cache
def _kernel32() -> ctypes.CDLL:
    """Return kernel32 with the signatures used here, so a handle is never cut to 32 bits.

    A private WinDLL, not ``ctypes.windll``, so setting restype here changes no one else's view.
    The platform check sits in this function because mypy only allows WinDLL inside one.
    """
    if sys.platform == "win32":
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = ctypes.c_void_p
        dll.CreateJobObjectW.restype = handle
        dll.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        dll.OpenProcess.restype = handle
        dll.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        dll.AssignProcessToJobObject.argtypes = (handle, handle)
        dll.QueryInformationJobObject.argtypes = (
            handle,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
        )
        dll.TerminateJobObject.argtypes = (handle, ctypes.c_uint)
        dll.CloseHandle.argtypes = (handle,)
        return dll
    else:
        raise OSError("Job Objects exist on Windows only")


def _new_job_holding(pid: int) -> int:
    """Create a job and put ``pid`` in it; return the job's handle.

    Raises:
        OSError: Either call failed; nothing is left open.
    """
    kernel32 = _kernel32()
    # SAFETY: documented Win32 calls on handles this function owns and closes on every path.
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _last_error()
    process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    try:
        if not process or not kernel32.AssignProcessToJobObject(job, process):
            raise _last_error()
    except OSError:
        _close(job)
        raise
    finally:
        if process:
            _close(process)
    return int(job)


def _active_processes(job: int) -> int:
    """Return how many members of ``job`` have not ended; 1 if the job cannot be read."""
    info = _Accounting()
    # SAFETY: a side-effect-free query into a struct sized by ctypes itself.
    if not _kernel32().QueryInformationJobObject(
        job, _BASIC_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        return 1  # Unreadable: keep the lease's record, the safe answer for a later release.
    return int(info.ActiveProcesses)


def _terminate(job: int) -> bool:
    """End every process in ``job``; False if the kernel refused."""
    # SAFETY: ends only the members of a job this packet made for one exec.
    return bool(_kernel32().TerminateJobObject(job, _KILLED_EXIT_CODE))


def _last_error() -> OSError:
    """Return the error the last failed kernel32 call on this thread left."""
    # Guarded for mypy's Linux run, where ctypes has neither name; see _kernel32.
    if sys.platform == "win32":
        return ctypes.WinError(ctypes.get_last_error())
    else:
        return OSError("Job Objects exist on Windows only")


def _close(handle: int) -> None:
    """Close a kernel handle this module opened."""
    _kernel32().CloseHandle(handle)
