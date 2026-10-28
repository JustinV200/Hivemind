"""Provide WindowsShim: LocalAppData paths, a Job Object per program and a Scheduled Task.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its data in a
``pollen`` folder under the user's LocalAppData on Windows, starts each command in a new process
group and in a Job Object of its own (the Windows stand-in for a POSIX process group; a Real Cell
is an existing device the Hive borrows for a task, and nothing a lease started may outlive it), so
a child that outlives its command is still seen and killed. A process whose job this packet does
not hold (it was started before a restart, or the CLI asks from another process) is followed
through its tree instead and ended with ``taskkill /T``. Nothing it starts opens a window on the
operator's desktop: every program, the on-device Warden included, gets a console of its own with
no window, so nothing it does to its console can reach the packet, and is asked to stop politely
by a short-lived helper that raises the break event on that console (pollen.platform.console).
The packet is installed as a
per-user Scheduled Task that runs at logon under a console host with no window, and writes its
log to a file in the data directory.
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
import platform
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

import psutil

from pollen.platform.base import (
    CONSOLE_SIGNAL_VERB,
    LOG_TO_FILE_OPTION,
    CommandRunner,
    PlatformFacts,
    ProcessRef,
    ServicePlan,
    ServiceResult,
    SpawnSpec,
    own_command,
    own_interpreter,
    run_command,
)
from pollen.platform.console import BREAK, CTRL_C, accept_interrupts
from pollen.platform.job_objects import CONSOLE_HOST, SUSPENDED_FLAG, JobTable
from waggle.messages.labels import OsFamily
from waggle.messages.session import ProcessSignal

TASK_NAME = "HiveMind Pollen"  # The Scheduled Task's name, as schtasks is given it.
CREATE_TIME_TOLERANCE_S = (
    1.0  # psutil's creation time is stable per process; this absorbs rounding.
)
# Windows-only constants, read by name so the module imports (and is tested) on every host; the
# fallbacks are never used on Windows itself.
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
        """Start ``spec`` in a new process group and Job Object, with no console window.

        Args:
            spec: The program, directory, environment and standard streams.

        Returns:
            The started process.

        Raises:
            OSError: The program could not be started, or not inside its job.
        """
        # An argument list, never a shell: no expansion or quoting happens to a request's argv.
        # Started suspended, so it cannot start anything before it is inside its job. A console of
        # its own with no window, the Warden's too: no command pops up on the operator's desktop,
        # and no control event it (or anything it starts) raises reaches the packet.
        process = await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=spec.cwd,
            env=dict(spec.env),
            stdin=spec.stdin,
            stdout=spec.stdout,
            stderr=spec.stderr,
            creationflags=_NEW_PROCESS_GROUP | SUSPENDED_FLAG | _NO_WINDOW,
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
            signal_: INTERRUPT (the break event, then a polite taskkill, then forced),
                TERMINATE (a polite taskkill, then the break event, then forced) or KILL (forced:
                the job, else ``taskkill /T /F``). The break event goes through the helper, on the
                program's own console.

        Returns:
            True if the signal was delivered; False if the tree was already gone or even the
            forced end failed.
        """
        if not self.group_alive(ref):
            return False
        if signal_ is not ProcessSignal.KILL:
            # Ctrl+Break first for an interrupt, as at a terminal; a polite taskkill first for a
            # terminate, which closes a program's windows and refuses one that has none.
            polite = (_break, _close) if signal_ is ProcessSignal.INTERRUPT else (_close, _break)
            if any(ask(self._runner, ref) for ask in polite):
                return True
            # Nothing reached it: it is ended as an unhandled SIGTERM would end it on POSIX.
        return _force(self._jobs, self._runner, ref)

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
        """Create the Scheduled Task that runs the packet at logon with no window, and start it.

        Args:
            plan: The command to run and the data directory it uses.

        Returns:
            The result; ``is_active`` true when schtasks created and started the task, else the
            command that starts the packet by hand.
        """
        return _install(self._runner, plan, self._env.get("SYSTEMROOT", "C:\\Windows"))

    def uninstall_service(self) -> ServiceResult:
        """Delete the Scheduled Task; ending the packet it runs is pollen.teardown's job.

        Returns:
            Whether a task was there to delete.
        """
        return _uninstall(self._runner)

    def accept_interrupts(self) -> None:
        """Let Ctrl+C reach the packet, which may have inherited "ignore Ctrl+C" at logon."""
        accept_interrupts()

    def ask_to_stop(self, pid: int) -> bool:
        """Press Ctrl+C on the console of ``pid`` (the packet), through the helper.

        False when it has gone, has no console, or shares it with anything but its own family (an
        operator's terminal), so nothing was pressed.
        """
        try:
            created = psutil.Process(pid).create_time()
        except psutil.Error:
            return False
        return _helper(self._runner, CTRL_C, ProcessRef(pid, created))


def _install(runner: CommandRunner, plan: ServicePlan, system_root: str) -> ServiceResult:
    """Create and start the task for ``plan``; on refusal, return the command to run by hand."""
    command = subprocess.list2cmdline(list(plan.command))
    hidden = _windowless(plan, system_root)
    created = runner(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", hidden, "/SC", "ONLOGON", "/F"]
    )
    if created.returncode != 0:
        return ServiceResult(
            unit_path=None,
            is_active=False,
            manual_command=command,
            detail="The Scheduled Task could not be created here; start the packet by hand "
            "with the command shown.",
        )
    runner(["schtasks", "/Run", "/TN", TASK_NAME])
    return ServiceResult(None, True, None, "The packet is installed as a Scheduled Task.")


def _uninstall(runner: CommandRunner) -> ServiceResult:
    """Delete the task by its name, touching nothing else; the packet it runs is not ended here."""
    # No /End: that ends the console host, not the packet below it, and takes away the console
    # the CLI presses Ctrl+C on to stop the packet cleanly (pollen.teardown does that next).
    deleted = runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    detail = (
        "The Scheduled Task was removed."
        if deleted.returncode == 0
        else "There was no Scheduled Task to remove."
    )
    return ServiceResult(unit_path=None, is_active=False, manual_command=None, detail=detail)


def _windowless(plan: ServicePlan, system_root: str) -> str:
    """Return the task's command line: ``plan``'s command under a console host with no window.

    Nothing appears at logon, yet the packet keeps a console for the Warden to share. Nobody sees
    that console, so the packet is told to log to a file.
    """
    host = PureWindowsPath(system_root, *CONSOLE_HOST)
    return subprocess.list2cmdline([str(host), "--headless", *plan.command, LOG_TO_FILE_OPTION])


def _close(runner: CommandRunner, ref: ProcessRef) -> bool:
    """Ask the tree of ``ref`` to close its windows (a polite taskkill); False if it has none."""
    # /T takes the whole tree, the nearest thing Windows has to signalling a POSIX group.
    return runner(["taskkill", "/T", "/PID", str(ref.pid)]).returncode == 0


def _break(runner: CommandRunner, ref: ProcessRef) -> bool:
    """Raise Ctrl+Break for the process group ``ref`` leads, on its own console, via the helper."""
    return _helper(runner, BREAK, ref)


def _helper(runner: CommandRunner, event: str, ref: ProcessRef) -> bool:
    """Run this ``pollen`` once, windowless, to raise ``event`` on the console of ``ref``.

    The creation time travels too, so the helper never raises it on a recycled pid's console.
    """
    argv = [*own_command(), CONSOLE_SIGNAL_VERB, event, str(ref.pid), repr(ref.created)]
    return runner(argv).returncode == 0


def _force(jobs: JobTable, runner: CommandRunner, ref: ProcessRef) -> bool:
    """End the tree of ``ref`` at once; True if it was ended."""
    # The job reaches a child whose leader has gone, which taskkill /T cannot.
    killed = jobs.kill(ref)
    if killed is not None:
        return killed
    return runner(["taskkill", "/T", "/F", "/PID", str(ref.pid)]).returncode == 0


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
