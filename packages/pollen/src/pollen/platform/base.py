"""Define PlatformShim, the one interface behind every operating-system difference the packet has.

The Pollen Packet (the thin gateway program on a device the Hive borrows) must run on Linux,
macOS and Windows, and the Hive learns what a device is from the packet's own report (a Real Cell
is an existing device the Hive borrows for a task and leaves as found). Everything that differs
by operating system sits behind this Protocol: where the data directory lives, how a child
process is started in its own process group, how a whole group is probed and signalled, which
facts the device reports about itself, how the packet is installed as a service that starts
at login, and, for colonization, which commands might start a Python interpreter here and where a
virtual environment keeps its programs. No other pollen module branches on the operating system,
and the single place that picks an implementation is ``pollen.platform.select``. External commands
(``systemctl``, ``launchctl``, ``schtasks``) go through an injected ``CommandRunner`` so the
install logic is tested on any host by recording the commands it would run. The one thing that
differs by how the packet was built rather than by operating system sits here too, beside
``launch_command`` and ``own_interpreter``: ``child_environment``, what a program the packet
starts inherits. A single-file build's bootloader points the dynamic loader at the libraries it
unpacked for itself, and a system program started with that path loads the packet's copies of
OpenSSL, zlib and the rest instead of the device's own, which fails on a distribution whose
versions differ from the ones the packet was built with. Taking the path away is not the whole
job, because the bootloader's other bookkeeping tells a single-file program started below it (the
packet itself, run by an operator's command) that it belongs to this instance, so it must be told
to start on its own.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Implemented by the Linux,
    macOS and Windows shims, chosen by pollen.platform.select, and called by the executors, the
    lease code, the capability probes and the install and uninstall commands of pollen.cli; calls
    into waggle (the signal and OS-family enums) and the standard library only.

Key invariants:
    - A group is addressed by a ``ProcessRef`` (pid plus the creation time the process had when
      the lease recorded it), so a recycled pid is never mistaken for a process the lease started.
    - ``run_command`` never raises for a missing program or a hung one: it reports a result with a
      return code, so an absent service manager is a branch, not a crash.
    - Nothing here runs a command through a shell or builds one from request text.
    - A program the packet starts, for a lease or for itself, is given ``child_environment`` of the
      packet's own environment (with a request's variables laid over it), never that environment
      as it is: the device's programs see the device's loader path, not the packet's.

See Also:
    - docs/waggle/gateway-contract.md sections 3 and 11 for the process model a gateway needs.
    - pollen.platform.select for the one place that branches on the operating system.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from waggle.clock import Clock
from waggle.messages.labels import OsFamily
from waggle.messages.session import ProcessSignal

COMMAND_TIMEOUT_S = 30.0  # A service manager answers in well under this; a hung one is reported.
COMMAND_UNAVAILABLE = 127  # The return code run_command reports for a program that is absent.
KILL_GRACE_S = 0.5  # How long a graceful stop waits for a group to leave before forcing it.
GROUP_POLL_S = 0.05  # How often a stop looks to see whether the group has gone.
GROUP_REAP_WAIT_S = 2.0  # The longest a forced stop waits for the kernel to finish the group.
EXIT_NO_RESTART = 78  # sysexits EX_CONFIG: the packet stopped for a reason a restart cannot fix.
# Variables that make a child interpreter use the packet's libraries instead of its own.
_INTERPRETER_VARIABLES = frozenset({"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONSTARTUP"})
# What a single-file build's bootloader changes for its children, and where it keeps the original.
_LOADER_VARIABLES = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH")
# PyInstaller's documented switch (6.9 and later; the build pins its version) for a single-file
# program started by another one: set, the child starts as an instance of its own.
_FROZEN_RESET = "PYINSTALLER_RESET_ENVIRONMENT"

__all__ = [
    "COMMAND_TIMEOUT_S",
    "COMMAND_UNAVAILABLE",
    "EXIT_NO_RESTART",
    "GROUP_POLL_S",
    "GROUP_REAP_WAIT_S",
    "KILL_GRACE_S",
    "CommandResult",
    "CommandRunner",
    "PlatformFacts",
    "PlatformShim",
    "ProcessRef",
    "ServicePlan",
    "ServiceResult",
    "SpawnSpec",
    "child_environment",
    "launch_command",
    "own_interpreter",
    "run_command",
    "stop_group",
]


@dataclass(frozen=True, slots=True)
class ProcessRef:
    """One process a lease started: its pid and the creation time it had when it was recorded."""

    pid: int  # Also the process-group id, since every child is spawned as its own group leader.
    created: float  # Seconds since the epoch as psutil reports; 0.0 means unknown (not checked).


@dataclass(frozen=True, slots=True)
class SpawnSpec:
    """Everything a shim needs to start one child in its own process group."""

    argv: tuple[str, ...]  # The program and its arguments; never a shell string.
    cwd: Path  # The child's working directory, already resolved and checked.
    env: Mapping[str, str]  # The child's whole environment, the overlay already applied.
    stdin: int | IO[bytes] | None  # asyncio.subprocess.PIPE, DEVNULL or None.
    stdout: (
        int | IO[bytes] | None
    )  # A pipe for an exec, a log file or DEVNULL for a background one.
    stderr: int | IO[bytes] | None  # As stdout.
    # Whether the child may go on running after the packet itself ends: the device's Warden
    # may (a restarted packet adopts it); a lease's program may not. Only Windows acts on it
    # (its Job Object's kill-on-close limit); a POSIX group is never tied to the packet's life.
    outlives_packet: bool = False


@dataclass(frozen=True, slots=True)
class PlatformFacts:
    """What the device reports about itself at enrolment; every later decision reads this."""

    os_family: OsFamily  # LINUX, MACOS or WINDOWS.
    distribution: str | None  # "Ubuntu 24.04", "macOS 14.5", "Windows 11"; None when unknown.
    architecture: str  # "x86_64", "aarch64", "arm64".
    package_manager: str | None  # apt, dnf, pacman, brew, winget; None when none is found.
    shell: str  # The login shell a user would type commands into.
    has_display: bool  # A display is attached to the current session.
    can_start_display: bool  # A virtual display (Xvfb) can be started on demand.
    has_audio: bool  # The audio programs the Exoskeleton's sound server needs are present.
    has_browser: bool  # A browser is installed.


@dataclass(frozen=True, slots=True)
class ServicePlan:
    """What ``pollen install`` asks a shim to set up."""

    command: tuple[str, ...]  # The argv that runs the packet: ``pollen run --data-dir ...``.
    data_dir: Path  # Where the packet keeps its state, for the service's description.


@dataclass(frozen=True, slots=True)
class ServiceResult:
    """What an install or uninstall did, in words the CLI prints."""

    unit_path: Path | None  # The unit, plist or task definition written or removed; None if none.
    is_active: bool  # The service manager now runs (or no longer runs) the packet.
    manual_command: str | None  # What the operator types to start it when no manager is there.
    detail: str  # One sentence for the operator.


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one external command."""

    returncode: int  # The exit code, or COMMAND_UNAVAILABLE for a program that is not there.
    output: str  # Standard output and error together, stripped; never fed back to a shell.


CommandRunner = Callable[[Sequence[str]], CommandResult]  # What a shim runs service commands with.


def run_command(argv: Sequence[str]) -> CommandResult:
    """Run one external command, capturing its output, without ever raising for its absence.

    Args:
        argv: The program and its arguments; run directly, never through a shell.

    Returns:
        The return code and the combined output; COMMAND_UNAVAILABLE when the program is missing
        or did not finish within COMMAND_TIMEOUT_S.
    """
    try:
        # Local, fast service-manager command with a bounded wait; a hang is reported below.
        # The device's own systemctl must load the device's libraries, not the ones a frozen
        # packet unpacked for itself.
        completed = subprocess.run(  # noqa: S603  # SAFETY: argv list, no shell, fixed programs.
            list(argv),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
            env=child_environment(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return CommandResult(returncode=COMMAND_UNAVAILABLE, output="")
    return CommandResult(completed.returncode, f"{completed.stdout}{completed.stderr}".strip())


def child_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Return the environment a program the packet starts runs with.

    Args:
        base: The packet's own environment. A request's variables are laid over the result, not
            over this, so a loader path a request names is the request's own choice and is kept.

    Returns:
        A copy without the variables that would point a child interpreter at the packet's
        libraries and, for a frozen build, with the loader paths its bootloader set put back and
        a program that is itself a single-file build told to start as an instance of its own.
    """
    env = {name: value for name, value in base.items() if name not in _INTERPRETER_VARIABLES}
    # A single-file build points the dynamic loader at the libraries it unpacked and keeps the
    # original value in NAME_ORIG; a system program started with the unpacked ones can fail to
    # load, or load the packet's copy of a library where it needs the device's.
    if getattr(sys, "frozen", False):
        for name in _LOADER_VARIABLES:
            original = env.pop(f"{name}_ORIG", None)
            if original is not None:
                env[name] = original
            else:
                env.pop(name, None)
        # The bootloader also leaves bookkeeping that makes another single-file program started
        # below it (the packet itself, run by an operator's command in a lease) believe it is a
        # worker of this instance: it then reuses these unpacked libraries through the very
        # loader path removed above, and fails to import its own. PyInstaller's documented
        # switch starts such a program as an independent instance.
        env[_FROZEN_RESET] = "1"
    return env


def launch_command(data_dir: Path) -> tuple[str, ...]:
    """Return the argv that starts ``pollen run`` for ``data_dir`` the way this process started.

    Args:
        data_dir: The data directory the service should run against.

    Returns:
        The packaged binary's own path when frozen, else the interpreter running ``-m pollen``.
    """
    # A single-file build is its own entry point; a normal install is the interpreter that
    # imported this very package, so the unit never depends on what is on PATH.
    base = (sys.executable,) if getattr(sys, "frozen", False) else (sys.executable, "-m", "pollen")
    return (*base, "run", "--data-dir", str(data_dir))


def own_interpreter() -> tuple[tuple[str, ...], ...]:
    """Return the interpreter running this process as a candidate to build a runtime with.

    Returns:
        This process's interpreter as one command, unless it is a frozen single-file build, which
        has no interpreter of its own to offer (an empty tuple).
    """
    # A PyInstaller binary's sys.executable is the packet itself, which cannot run ``-m venv``.
    return () if getattr(sys, "frozen", False) else ((sys.executable,),)


async def stop_group(
    shim: PlatformShim,
    ref: ProcessRef,
    *,
    graceful: bool,
    clock: Clock,
    grace_s: float = KILL_GRACE_S,
) -> bool:
    """End a whole process group: ask politely when ``graceful``, then force it.

    Args:
        shim: The platform shim that signals the group.
        ref: A process the lease started.
        graceful: Send TERMINATE first and wait up to ``grace_s`` for the group to leave; an
            immediate stop forces it at once.
        clock: Sleeps between looks at the group.
        grace_s: How long a graceful stop waits before forcing the group; a lease's program gets
            the short default, the on-device Warden, which has a lease to give back, a long one.

    Returns:
        True if the group was alive when asked; False if there was nothing to stop.
    """
    if not shim.group_alive(ref):
        return False
    if graceful:
        shim.signal_group(ref, ProcessSignal.TERMINATE)
        await _wait_gone(shim, ref, grace_s, clock)
    # Forced even after a polite exit: a child the leader started may have outlived it.
    shim.signal_group(ref, ProcessSignal.KILL)
    await _wait_gone(shim, ref, GROUP_REAP_WAIT_S, clock)
    return True


async def _wait_gone(shim: PlatformShim, ref: ProcessRef, limit_s: float, clock: Clock) -> None:
    """Look at the group every GROUP_POLL_S until it is gone or ``limit_s`` has passed."""
    waited = 0.0
    while waited < limit_s and shim.group_alive(ref):
        await clock.sleep(GROUP_POLL_S)
        waited += GROUP_POLL_S


class PlatformShim(Protocol):
    """Every operating-system difference the packet has, behind one seam."""

    @property
    def name(self) -> str:
        """The shim's short name: linux, macos or windows."""
        ...

    def default_data_dir(self) -> Path:
        """Return where the packet keeps its data when no directory is given.

        Returns:
            The conventional per-user directory for this platform.
        """
        ...

    async def spawn(self, spec: SpawnSpec) -> asyncio.subprocess.Process:
        """Start ``spec`` as the leader of a new process group (POSIX) or process group (Windows).

        Args:
            spec: The program, directory, environment and standard streams.

        Returns:
            The started process.

        Raises:
            OSError: The program could not be started.
        """
        ...

    def process_ref(self, pid: int) -> ProcessRef:
        """Capture the identity of a process just started.

        Args:
            pid: The pid ``spawn`` returned.

        Returns:
            The pid with the creation time the process has now.
        """
        ...

    def group_alive(self, ref: ProcessRef) -> bool:
        """Say whether any process of ``ref``'s group is still running.

        Args:
            ref: A process the lease started.

        Returns:
            True while the leader, or a process it started, lives.
        """
        ...

    def signal_group(self, ref: ProcessRef, signal: ProcessSignal) -> bool:
        """Deliver a platform-neutral signal to the whole group of ``ref``.

        Args:
            ref: A process the lease started; nothing else is ever signalled.
            signal: INTERRUPT, TERMINATE or KILL, mapped to what the platform has.

        Returns:
            True if a signal was delivered; False if the group was already gone.
        """
        ...

    def facts(self) -> PlatformFacts:
        """Probe what this device is, for the enrolment report.

        Returns:
            The operating system, distribution, architecture, package manager, shell and flags.
        """
        ...

    def python_candidates(self) -> tuple[tuple[str, ...], ...]:
        """Return the commands that might start a Python interpreter here, best first.

        Returns:
            Each command as an argument list (``("py", "-3.12")`` is one); the colonization step
            asks each for its version and takes the first that is new enough.
        """
        ...

    def venv_executable(self, venv: Path, name: str) -> Path:
        """Return where a virtual environment at ``venv`` puts the program ``name``.

        Args:
            venv: The environment's directory.
            name: A program the environment installs: ``python``, or a console script.

        Returns:
            The path of that program, whose layout (``bin`` or ``Scripts``) differs by platform.
        """
        ...

    def install_service(self, plan: ServicePlan) -> ServiceResult:
        """Write the unit that runs the packet at login and start it where a manager exists.

        Args:
            plan: The command to run and the data directory it uses.

        Returns:
            What was written and whether the service manager started it; where there is no
            manager the result carries the command to start it by hand.
        """
        ...

    def uninstall_service(self) -> ServiceResult:
        """Stop and remove the unit, touching nothing of the operator's.

        Returns:
            What was removed.
        """
        ...
