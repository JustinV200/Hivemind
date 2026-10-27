"""Provide WindowsShim: LocalAppData paths, a Job Object per program and a Scheduled Task.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its data in a
``pollen`` folder under the user's LocalAppData on Windows, starts each command in a new process
group so a break signal can reach it and in a Job Object of its own (the Windows stand-in for a
POSIX process group; a Real Cell is an existing device the Hive borrows for a task, and nothing a
lease started may outlive it), so a child that outlives its command is still seen and killed. A
process whose job this packet does not hold (it was started before a restart, or the CLI asks
from another process) is followed through its tree instead and ended with ``taskkill /T``. It is
installed as a per-user Scheduled Task that runs at logon, which needs no administrator rights.
The shim is written to run on any host: the Windows-only constants are read with ``getattr`` and
every external command goes through the injected runner, so the install logic is tested by
recording the ``schtasks`` commands it would run.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Chosen by
    pollen.platform.select on Windows; called by the executors, the lease code and pollen.cli
    through the PlatformShim Protocol; calls into pollen.platform.job_objects, psutil and the
    standard library only.

Key invariants:
    - The task is one entry named after the packet; uninstalling ends and deletes that entry and
      nothing of the operator's, the data directory included.
    - A process is signalled only if its pid and creation time still match the lease's record.

See Also:
    - docs/waggle/gateway-contract.md section 3 (the Job Object note) and section 11.
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
from pollen.platform.job_objects import SUSPENDED_FLAG, JobTable
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
    """The Windows platform: LocalAppData, taskkill process trees and a Scheduled Task."""

    name = "windows"

    def __init__(
        self,
        home: Path,
        env: Mapping[str, str] | None = None,
        runner: CommandRunner = run_command,
    ) -> None:
        """Bind the shim to a home directory and environment.

        Args:
            home: The account's profile directory.
            env: The environment to read LOCALAPPDATA and COMSPEC from; empty when None.
            runner: Runs taskkill and schtasks; tests pass a recorder.
        """
        self._home = home
        self._env = {} if env is None else env
        self._runner = runner
        self._jobs = JobTable()  # The job of every program spawn started in this process.

    def default_data_dir(self) -> Path:
        """Return the ``pollen`` folder under LOCALAPPDATA, or under the profile's AppData/Local.

        Returns:
            The conventional per-user data directory.
        """
        base = self._env.get("LOCALAPPDATA") or str(self._home / "AppData" / "Local")
        return Path(base) / "pollen"

    async def spawn(self, spec: SpawnSpec) -> asyncio.subprocess.Process:
        """Start ``spec`` in a new process group, inside a Job Object of its own on Windows.

        Args:
            spec: The program, directory, environment and standard streams.

        Returns:
            The started process.

        Raises:
            OSError: The program could not be started, or not inside its job.
        """
        # An argument list, never a shell: no expansion or quoting happens to a request's argv.
        # Started suspended, so it cannot start anything before it is inside its job.
        process = await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=spec.cwd,
            env=dict(spec.env),
            stdin=spec.stdin,
            stdout=spec.stdout,
            stderr=spec.stderr,
            creationflags=_NEW_PROCESS_GROUP | SUSPENDED_FLAG,
        )
        # Job Objects exist on Windows only; elsewhere (CI runs this shim on Linux) the flag above
        # is 0 and the program already runs.
        if SUSPENDED_FLAG:
            await self._jobs.contain(process)
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
            True while anything in its job runs or, for a job this process does not hold, while
            the leader (same creation time) or one of its descendants lives.
        """
        contained = self._jobs.alive(ref)
        return _tree_alive(ref) if contained is None else contained

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
        if signal_ is ProcessSignal.KILL:
            # The job reaches a child whose leader has gone, which taskkill /T cannot.
            killed = self._jobs.kill(ref)
            if killed is not None:
                return killed
        flags = ["/F"] if signal_ is ProcessSignal.KILL else []
        # /T takes the whole tree, the nearest thing Windows has to signalling a POSIX group.
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
    """Say whether the leader of ``ref`` or a process below it runs, for a job this shim lacks.

    The fallback for a process started before a packet restart, or asked about from another
    process: a child whose leader has exited is no longer below it, so this can miss one.
    """
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
