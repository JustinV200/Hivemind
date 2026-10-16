"""Provide WindowsShim: LocalAppData paths, process trees in Job Objects and a Scheduled Task.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its data in a
``pollen`` folder under the user's LocalAppData on Windows, starts each command in a new process
group so a break signal can reach it and in a Job Object of its own so the whole tree, detached
descendants included, is seen and ended as one (the Windows counterpart of a POSIX process group;
a Real Cell is an existing device the Hive borrows for a task, and nothing a lease started may
outlive it). Where the host refuses a job (a packet already inside a job that forbids nesting) the
tree is ended with ``taskkill /T`` instead, which reaches what is still linked to the leader. It is
installed as a per-user Scheduled Task that runs at logon, which needs no administrator rights.
The shim is written to run on any host: the Windows-only constants are read with ``getattr``,
kernel32 is bound only on Windows and every external command goes through the injected runner,
so the job rules and the install logic are tested with a fake kernel32 and by recording the
``schtasks`` commands it would run.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Chosen by
    pollen.platform.select on Windows; called by the executors, the lease code and pollen.cli
    through the PlatformShim Protocol; calls into psutil, ctypes (through ``jobs``) and the
    standard library only.

Key invariants:
    - The task is one entry named after the packet; uninstalling ends and deletes that entry and
      nothing of the operator's, the data directory included.
    - A process is signalled only if its pid and creation time still match the lease's record.
    - A child in a job is alive while its job holds any process and is killed through the job;
      the process table and taskkill are consulted only for a child no job could be made for.

See Also:
    - docs/waggle/gateway-contract.md section 3 (the Job Object requirement) and section 11.
    - pollen.platform.windows.jobs for the Job Object table.
    - pollen.platform.posix for the POSIX behaviour this shim parallels.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path

import psutil

from pollen.platform.base import (
    CommandRunner,
    PlatformFacts,
    ProcessRef,
    ServicePlan,
    ServiceResult,
    SpawnSpec,
    own_interpreter,
    run_command,
)
from pollen.platform.windows.jobs import JobObjects, host_kernel32
from waggle.messages.labels import OsFamily
from waggle.messages.session import ProcessSignal

TASK_NAME = "HiveMind Pollen"  # The Scheduled Task's name, as schtasks is given it.
CREATE_TIME_TOLERANCE_S = (
    1.0  # psutil's creation time is stable per process; this absorbs rounding.
)
# Windows-only constants, read by name so the module imports (and is tested) on every host; the
# fallbacks are never used on Windows itself.
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)

__all__ = ["TASK_NAME", "WindowsShim"]


class WindowsShim:
    """The Windows platform: LocalAppData, Job Object process trees and a Scheduled Task."""

    name = "windows"

    def __init__(
        self,
        home: Path,
        env: Mapping[str, str] | None = None,
        runner: CommandRunner = run_command,
        jobs: JobObjects | None = None,
    ) -> None:
        """Bind the shim to a home directory and environment.

        Args:
            home: The account's profile directory.
            env: The environment to read LOCALAPPDATA and COMSPEC from; empty when None.
            runner: Runs taskkill and schtasks; tests pass a recorder.
            jobs: The Job Object table; the host's kernel32 when None (and so, off Windows, a
                table that holds nothing). Tests pass one over a fake kernel32.
        """
        self._home = home
        self._env = {} if env is None else env
        self._runner = runner
        self._jobs = JobObjects(host_kernel32()) if jobs is None else jobs

    def default_data_dir(self) -> Path:
        """Return the ``pollen`` folder under LOCALAPPDATA, or under the profile's AppData/Local.

        Returns:
            The conventional per-user data directory.
        """
        base = self._env.get("LOCALAPPDATA") or str(self._home / "AppData" / "Local")
        return Path(base) / "pollen"

    async def spawn(self, spec: SpawnSpec) -> asyncio.subprocess.Process:
        """Start ``spec`` in a new process group and a Job Object of its own.

        Args:
            spec: The program, directory, environment and standard streams.

        Returns:
            The started process.

        Raises:
            OSError: The program could not be started.
        """
        # An argument list, never a shell: no expansion or quoting happens to a request's argv.
        process = await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=spec.cwd,
            env=dict(spec.env),
            stdin=spec.stdin,
            stdout=spec.stdout,
            stderr=spec.stderr,
            creationflags=_NEW_PROCESS_GROUP,
        )
        # Into its job before anyone else sees it, so every descendant is born into the job; a
        # refusal is noted by the table and the tree is then handled by taskkill and psutil.
        self._jobs.enroll(self.process_ref(process.pid), outlives_packet=spec.outlives_packet)
        return process

    def process_ref(self, pid: int) -> ProcessRef:
        """Capture ``pid`` together with its creation time.

        Args:
            pid: The pid ``spawn`` returned.

        Returns:
            The reference; its creation time is 0.0 if the process vanished already.
        """
        try:
            return ProcessRef(pid, psutil.Process(pid).create_time())
        except psutil.Error:
            return ProcessRef(pid, 0.0)

    def group_alive(self, ref: ProcessRef) -> bool:
        """Say whether the process of ``ref``, or any process below it, still runs.

        Args:
            ref: A process the lease started.

        Returns:
            True while the leader (same creation time) or one of its descendants lives.
        """
        # The job knows every descendant, detached or reparented; the process table only knows
        # the ones still linked to the leader, and serves a child no job could be made for.
        in_job = self._jobs.alive(ref)
        return _tree_alive(ref) if in_job is None else in_job

    def signal_group(self, ref: ProcessRef, signal_: ProcessSignal) -> bool:
        """Deliver ``signal_`` to the process tree of ``ref``.

        Args:
            ref: A process the lease started; nothing else is ever signalled.
            signal_: INTERRUPT (a break event), TERMINATE (a polite taskkill, then the break
                event if that could not end it) or KILL (forced).

        Returns:
            True if the signal was delivered; False if the tree was already gone or no way of
            asking reached it.
        """
        if not self.group_alive(ref):
            return False
        if signal_ is ProcessSignal.INTERRUPT:
            return _send_break(ref.pid)
        # A forced stop goes through the job, which ends a detached descendant taskkill's walk
        # from the leader would miss; taskkill serves a child with no job, or a job that refused.
        if signal_ is ProcessSignal.KILL and self._jobs.terminate(ref):
            return True
        flags = ["/F"] if signal_ is ProcessSignal.KILL else []
        # /T takes the whole tree as far as its parent links reach.
        result = self._runner(["taskkill", "/T", *flags, "/PID", str(ref.pid)])
        if result.returncode == 0 or signal_ is ProcessSignal.KILL:
            return result.returncode == 0
        # A polite taskkill only closes windows, and a console program has none, so it refuses: the
        # program is asked with the break event its process group was made for, which ends it
        # unless it handles the event, as a SIGTERM would on POSIX.
        return _send_break(ref.pid)

    def python_candidates(self) -> tuple[tuple[str, ...], ...]:
        """Return the launcher and interpreter names a Windows host usually has, then our own.

        Returns:
            ``py -3.13``, ``py -3.12``, ``python`` and, unless this is a frozen binary, the
            interpreter running the packet.
        """
        return (("py", "-3.13"), ("py", "-3.12"), ("python",), *own_interpreter())

    def venv_executable(self, venv: Path, name: str) -> Path:
        """Return the ``Scripts`` entry of a Windows virtual environment for ``name``.

        Args:
            venv: The environment's directory.
            name: ``python`` or a console script.

        Returns:
            The path of that program.
        """
        return venv / "Scripts" / f"{name}.exe"

    def facts(self) -> PlatformFacts:
        """Probe the device for the enrolment report.

        Returns:
            Windows, its release, the architecture, the package manager and the shell found.
        """
        return PlatformFacts(
            os_family=OsFamily.WINDOWS,
            distribution=f"Windows {platform.release()}"[:64],
            architecture=platform.machine() or "unknown",
            package_manager=next(
                (m for m in ("winget", "choco", "scoop") if shutil.which(m)), None
            ),
            shell=self._env.get("COMSPEC", "cmd.exe"),
            has_display=True,  # A Windows session always has a desktop.
            can_start_display=False,  # The Exoskeleton there is the browser alone.
            has_audio=False,  # The Hive's sound server is a Linux program set.
            has_browser=True,  # Edge ships with the system.
        )

    def install_service(self, plan: ServicePlan) -> ServiceResult:
        """Create the Scheduled Task that runs the packet at logon, and start it now.

        Args:
            plan: The command to run and the data directory it uses.

        Returns:
            The result; ``is_active`` true when schtasks created and started the task, else the
            command that starts the packet by hand.
        """
        command = subprocess.list2cmdline(list(plan.command))
        created = self._runner(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command, "/SC", "ONLOGON", "/F"]
        )
        if created.returncode != 0:
            return ServiceResult(
                unit_path=None,
                is_active=False,
                manual_command=command,
                detail="The Scheduled Task could not be created here; start the packet by hand "
                "with the command shown.",
            )
        self._runner(["schtasks", "/Run", "/TN", TASK_NAME])
        return ServiceResult(None, True, None, "The packet is installed as a Scheduled Task.")

    def uninstall_service(self) -> ServiceResult:
        """End and delete the Scheduled Task.

        Returns:
            Whether a task was there to delete.
        """
        self._runner(["schtasks", "/End", "/TN", TASK_NAME])
        deleted = self._runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        detail = (
            "The Scheduled Task was removed."
            if deleted.returncode == 0
            else "There was no Scheduled Task to remove."
        )
        return ServiceResult(unit_path=None, is_active=False, manual_command=None, detail=detail)


def _tree_alive(ref: ProcessRef) -> bool:
    """Say whether the leader of ``ref`` (same creation time) or a child still linked to it runs."""
    try:
        leader = psutil.Process(ref.pid)
        if ref.created and abs(leader.create_time() - ref.created) > CREATE_TIME_TOLERANCE_S:
            return False  # The pid now belongs to something else.
        if leader.status() != psutil.STATUS_ZOMBIE:
            return True
        return any(child.is_running() for child in leader.children(recursive=True))
    except psutil.Error:
        return False


def _send_break(pid: int) -> bool:
    """Send the break event to the process group ``pid`` leads; False if it could not be sent.

    Every program the shim spawns leads a process group of its own, so the event reaches that
    group alone. It needs a console shared with the packet, and a packet with none cannot send it.
    """
    try:
        os.kill(pid, _BREAK_EVENT)
    except OSError:
        return False
    return True
