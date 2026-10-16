"""Hold each child a Windows device starts in a Job Object, so a release ends all of its tree.

On Windows a process group is only a grouping for the console break event, and ``taskkill /T``
ends a tree by walking parent links: a grandchild whose parent has already exited, or one started
detached, is not on that walk and would outlive the lease that started it. A Job Object is the
kernel's own answer (gateway-contract section 3): every descendant of a process in a job is born
into the job, the job can be terminated as one, and it can be asked how many of its processes are
still running. The Pollen Packet (the thin gateway program on a device the Hive borrows) puts each
child it starts in a fresh job the moment the child exists, holds the job's handle for as long as
the child's tree may live, kills the tree through the job and asks the job, not the process table,
whether the tree is gone. A job is created with the kill-on-close limit unless the caller says the
child may outlive the packet (the device's Warden, which a restarted packet adopts), so a lease's
tree cannot survive the packet itself. The kernel32 calls go through ``ctypes``; the slice of
kernel32 used is a Protocol so the table and its rules are tested on every host with a fake, and
a host where a job cannot be made or a child cannot be assigned (a packet already inside a job
that forbids nesting) is answered with False, so the shim falls back to ``taskkill``.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.platform.windows (the shim); calls into ctypes and the standard library only.

Key invariants:
    - A child is in its job before the shim hands the process back, so the one window in which a
      descendant could escape is the child's own first instructions.
    - A job handle is held until the job reports no process left, then closed; it is never
      closed while the tree may live, since closing it would end the tree (or, for a child that
      outlives the packet, lose the only way of reaching it).
    - A question about a pid the table does not hold, or holds for another creation time, is
      answered "not known" (None), never guessed at; the caller then uses the process table.
    - The real kernel32 is bound only on Windows; elsewhere the table holds nothing and answers
      None to everything.

See Also:
    - docs/waggle/gateway-contract.md section 3 for the Job Object requirement.
    - pollen.platform.windows for the shim that falls back to taskkill.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import _Pointer
from typing import Protocol, cast

from pollen.platform.base import ProcessRef

log = logging.getLogger(__name__)

# Win32 constants, from the SDK headers (winnt.h, processthreadsapi.h).
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000  # Ending the last handle ends every process in the job.
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # The information class that carries LimitFlags.
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1  # The class that counts the job's live processes.
PROCESS_SET_QUOTA = 0x0100  # The access AssignProcessToJobObject needs on the process, with...
PROCESS_TERMINATE = 0x0001  # ...this one.
JOB_EXIT_CODE = 1  # What a process killed through its job exits with (any non-zero will do).
CREATE_TIME_TOLERANCE_S = 1.0  # psutil's creation time is stable per process; absorbs rounding.

__all__ = [
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "BasicAccounting",
    "ExtendedLimits",
    "JobObjects",
    "Kernel32",
    "host_kernel32",
]


class _BasicLimits(ctypes.Structure):
    """JOBOBJECT_BASIC_LIMIT_INFORMATION, laid out as winnt.h declares it."""

    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),  # ULONG_PTR: pointer-sized, like SIZE_T.
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    )


class _IoCounters(ctypes.Structure):
    """IO_COUNTERS: six unsigned 64-bit counts."""

    _fields_ = tuple(
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    )


class ExtendedLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION: the basic limits, then the memory figures."""

    _fields_ = (
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )

    def limit_flags(self) -> int:
        """Return the LimitFlags the structure carries (what a test reads back)."""
        return int(self.BasicLimitInformation.LimitFlags)


class BasicAccounting(ctypes.Structure):
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: times, then the process counts."""

    _fields_ = (
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    )


class Kernel32(Protocol):
    """The slice of kernel32 the jobs use, with the Win32 names ctypes resolves them by.

    Handles are ints (a NULL return is None, as a ``c_void_p`` result comes back); a BOOL result
    is an int that is zero on failure. A test implements this with a fake.
    """

    def CreateJobObjectW(self, attributes: None, name: None) -> int | None:  # noqa: N802  # The Win32 name.
        """Create an anonymous job; None if the host refused."""
        ...

    def SetInformationJobObject(  # noqa: N802  # The Win32 name.
        self, job: int, info_class: int, info: _Pointer[ExtendedLimits], size: int
    ) -> int:
        """Set a limit on the job."""
        ...

    def AssignProcessToJobObject(self, job: int, process: int) -> int:  # noqa: N802  # The Win32 name.
        """Put a process (by handle) and its future descendants in the job."""
        ...

    def TerminateJobObject(self, job: int, exit_code: int) -> int:  # noqa: N802  # The Win32 name.
        """End every process in the job."""
        ...

    def QueryInformationJobObject(  # noqa: N802  # The Win32 name.
        self, job: int, info_class: int, info: _Pointer[BasicAccounting], size: int, returned: None
    ) -> int:
        """Fill ``info`` with the job's accounting."""
        ...

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int | None:  # noqa: N802  # The Win32 name.
        """Open a handle on ``pid`` with ``access``; None if it could not be opened."""
        ...

    def CloseHandle(self, handle: int) -> int:  # noqa: N802  # The Win32 name.
        """Release a handle."""
        ...


def host_kernel32() -> Kernel32 | None:
    """Bind the real kernel32 for the jobs on Windows; None on any other host.

    Returns:
        kernel32 with the argument and result types of the calls above declared, so a handle
        (pointer-sized) is never truncated to a C int, or None where there is no kernel32.
    """
    # mypy checks this module on Windows as well as Linux; the else keeps the Linux check from
    # reporting what follows an early return as unreachable (the posix shim's rule).
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle, dword, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        kernel32.CreateJobObjectW.restype = handle
        kernel32.SetInformationJobObject.argtypes = (handle, dword, ctypes.c_void_p, dword)
        kernel32.SetInformationJobObject.restype = boolean
        kernel32.AssignProcessToJobObject.argtypes = (handle, handle)
        kernel32.AssignProcessToJobObject.restype = boolean
        kernel32.TerminateJobObject.argtypes = (handle, dword)
        kernel32.TerminateJobObject.restype = boolean
        kernel32.QueryInformationJobObject.argtypes = (
            handle,
            dword,
            ctypes.c_void_p,
            dword,
            ctypes.c_void_p,
        )
        kernel32.QueryInformationJobObject.restype = boolean
        kernel32.OpenProcess.argtypes = (dword, boolean, dword)
        kernel32.OpenProcess.restype = handle
        kernel32.CloseHandle.argtypes = (handle,)
        kernel32.CloseHandle.restype = boolean
        return cast(Kernel32, kernel32)
    else:
        return None


class JobObjects:
    """The jobs of the children this packet started, by the pid each child had.

    Owns mutable state (codingrules 8.5, documented): the open job handles, each with the
    creation time of the child it was made for; an entry leaves when the job reports no process
    left. Touched from the loop's thread only.
    """

    def __init__(self, kernel32: Kernel32 | None) -> None:
        """Bind the table to a kernel32, or to none (every answer is then None).

        Args:
            kernel32: The host's kernel32 (``host_kernel32``), a fake, or None off Windows.
        """
        self._kernel32 = kernel32
        self._jobs: dict[int, tuple[int, float]] = {}  # pid -> (job handle, creation time).
        self._warned = (
            False  # The first refusal is worth an operator's attention; the rest are not.
        )

    def enroll(self, ref: ProcessRef, *, outlives_packet: bool) -> bool:
        """Put the child ``ref`` and its future descendants in a fresh job.

        Args:
            ref: The child just started, by pid and creation time.
            outlives_packet: Leave the kill-on-close limit off, so the tree survives the packet
                (the device's Warden); a lease's child dies with the packet.

        Returns:
            True when the child is in a job the table now holds; False when the host has no
            kernel32 or refused a step, in which case the shim keeps to taskkill for this child.
        """
        kernel32 = self._kernel32
        if kernel32 is None:
            return False
        job = kernel32.CreateJobObjectW(None, None)
        if job is None:
            return self._refused("be created")
        if (outlives_packet or self._kill_on_close(kernel32, job)) and self._assign(
            kernel32, job, ref.pid
        ):
            self._jobs[ref.pid] = (job, ref.created)
            return True
        kernel32.CloseHandle(job)
        return False

    def alive(self, ref: ProcessRef) -> bool | None:
        """Say whether any process is left in the job of ``ref``.

        Args:
            ref: A process the lease started.

        Returns:
            True while the job holds a process, False once it is empty (its handle is then
            closed), None when the table holds no job for ``ref``.
        """
        held = self._held(ref)
        if held is None or self._kernel32 is None:
            return None
        info = BasicAccounting()
        ok = self._kernel32.QueryInformationJobObject(
            held,
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.pointer(info),
            ctypes.sizeof(info),
            None,
        )
        if not ok:
            # A job that cannot be asked is one this table can no longer vouch for: let it go and
            # let the caller fall back to the process table.
            self._forget(ref.pid)
            return None
        if int(info.ActiveProcesses) > 0:
            return True
        self._forget(ref.pid)  # Empty for good: nothing can join a job with no process in it.
        return False

    def terminate(self, ref: ProcessRef) -> bool | None:
        """End every process in the job of ``ref``.

        Args:
            ref: A process the lease started.

        Returns:
            True when the job was told to end, False when the call failed, None when the table
            holds no job for ``ref``.
        """
        held = self._held(ref)
        if held is None or self._kernel32 is None:
            return None
        return bool(self._kernel32.TerminateJobObject(held, JOB_EXIT_CODE))

    def _held(self, ref: ProcessRef) -> int | None:
        """Return the job handle held for ``ref``, if its creation time is the child's."""
        entry = self._jobs.get(ref.pid)
        if entry is None:
            return None
        job, created = entry
        # A recorded pid from an earlier packet run can equal a pid this run gave a job to; the
        # creation times tell them apart. An unknown time (0.0) cannot be checked and is trusted.
        if ref.created and created and abs(ref.created - created) > CREATE_TIME_TOLERANCE_S:
            return None
        return job

    def _forget(self, pid: int) -> None:
        """Close and drop the job held for ``pid``."""
        job, _created = self._jobs.pop(pid)
        if self._kernel32 is not None:
            self._kernel32.CloseHandle(job)

    def _kill_on_close(self, kernel32: Kernel32, job: int) -> bool:
        """Set the kill-on-close limit on ``job``; False (and a note) if the host refused."""
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.pointer(limits),
            ctypes.sizeof(limits),
        )
        return bool(ok) or self._refused("take the kill-on-close limit")

    def _assign(self, kernel32: Kernel32, job: int, pid: int) -> bool:
        """Put ``pid`` in ``job``; False (and a note) if it could not be opened or assigned."""
        process = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if process is None:
            return self._refused("open the child")
        try:
            # Fails on a packet that is itself in a job forbidding nested jobs (Windows 7, or a
            # job with no breakaway right); the shim then keeps to taskkill for this child.
            return bool(kernel32.AssignProcessToJobObject(job, process)) or self._refused(
                "take the child"
            )
        finally:
            kernel32.CloseHandle(process)

    def _refused(self, step: str) -> bool:
        """Note that the host refused a step, loudly the first time, and answer False."""
        level = logging.DEBUG if self._warned else logging.WARNING
        self._warned = True
        log.log(level, "pollen.job_object_refused step=%s fallback=taskkill", step)
        return False
