"""Provide PosixShim, the process, probe and path behaviour Linux and macOS share.

The Pollen Packet (the thin gateway program on a device the Hive borrows) runs each command a
Warden (the per-Cell supervisor) sends as the leader of a process group of its own, so that one
signal reaches the command and every child it started. This base class holds that half of the
platform shim for the two POSIX systems: starting a child with a session of its own, telling
whether a group still has a live member, and signalling the whole group. It also takes care of the
dangerous case a long-lived gateway meets: a lease records a pid, the machine reboots or the
leader dies, and the pid is handed to an unrelated program. A ``ProcessRef`` carries the creation
time the process had when it was recorded, and nothing is signalled unless that still matches or
the process is a descendant created after the leader started. The Linux and macOS shims add the
data directory, the facts about the distribution and the service installation.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Extended by
    pollen.platform.linux and pollen.platform.macos; called through PlatformShim by the executors
    and the lease code; calls into psutil and the standard library only.

Key invariants:
    - Every child is started with ``start_new_session`` so its pid is also its group id.
    - A signal reaches a process only if it is the recorded leader (same creation time) or a
      live descendant of it that was created at or after the leader; a zombie counts as gone.
    - Probing never raises for a process that vanished mid-scan.

See Also:
    - docs/waggle/gateway-contract.md section 11 (process groups) and section 12 (after a restart).
    - pollen.platform.base for the Protocol and the value types.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import signal
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import psutil

from pollen.platform.base import (
    CommandRunner,
    PlatformFacts,
    ProcessRef,
    SpawnSpec,
    own_interpreter,
    run_command,
)
from waggle.messages.labels import OsFamily
from waggle.messages.session import ProcessSignal

CREATE_TIME_TOLERANCE_S = (
    1.0  # psutil's creation time is stable per process; this absorbs rounding.
)
_POSIX_SIGNALS = {
    ProcessSignal.INTERRUPT: signal.SIGINT,
    ProcessSignal.TERMINATE: signal.SIGTERM,
    ProcessSignal.KILL: signal.SIGKILL,
}
_X11_PROGRAMS = ("Xvfb", "xdotool", "import", "openbox")  # What the Exoskeleton's display needs.
_AUDIO_PROGRAMS = ("pulseaudio", "pactl", "parec", "paplay")  # What its sound server needs.
_BROWSERS = ("firefox", "google-chrome", "chromium", "chromium-browser")

__all__ = ["PosixShim"]


class PosixShim:
    """Process groups through sessions and killpg, shared by the Linux and macOS shims."""

    name = "posix"
    # What the subclass reports and probes; overridden by LinuxShim and MacOsShim.
    package_managers: ClassVar[tuple[str, ...]] = ()
    can_host_x11: ClassVar[bool] = False

    def __init__(
        self, home: Path, env: Mapping[str, str], runner: CommandRunner = run_command
    ) -> None:
        """Bind the shim to a home directory and environment.

        Args:
            home: The account's home directory; paths such as the unit file derive from it.
            env: The environment to read platform facts (SHELL, DISPLAY, XDG_*) from.
            runner: Runs service-manager commands; tests pass a recorder.
        """
        self._home = home
        self._env = env
        self._runner = runner

    async def spawn(self, spec: SpawnSpec) -> asyncio.subprocess.Process:
        """Start ``spec`` as the leader of a new session and process group.

        Args:
            spec: The program, directory, environment and standard streams.

        Returns:
            The started process; its pid is also its process-group id.

        Raises:
            OSError: The program could not be started.
        """
        # An argument list, never a shell: no expansion or quoting happens to a request's argv.
        return await asyncio.create_subprocess_exec(
            *spec.argv,  # SAFETY: an argument list, never shell=True.
            cwd=spec.cwd,
            env=dict(spec.env),
            stdin=spec.stdin,
            stdout=spec.stdout,
            stderr=spec.stderr,
            start_new_session=True,
        )

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
        """Say whether any process of ``ref``'s group is still running.

        Args:
            ref: A process the lease started.

        Returns:
            True while the leader, or a descendant created after it, lives.
        """
        # killpg(0) is a free "does this group exist": no member means nothing to check further.
        try:
            os.killpg(ref.pid, 0)
        except (ProcessLookupError, PermissionError):
            return False  # Empty group, or one that is not ours to signal.
        return bool(self._members(ref))

    def signal_group(self, ref: ProcessRef, signal_: ProcessSignal) -> bool:
        """Deliver ``signal_`` to the whole group of ``ref``.

        Args:
            ref: A process the lease started; nothing else is ever signalled.
            signal_: INTERRUPT, TERMINATE or KILL.

        Returns:
            True if the signal was delivered; False if the group was already gone.
        """
        if not self.group_alive(ref):
            return False
        try:
            os.killpg(ref.pid, _POSIX_SIGNALS[signal_])
        except (ProcessLookupError, PermissionError):
            return False  # It exited between the check and the signal: nothing left to do.
        return True

    def python_candidates(self) -> tuple[tuple[str, ...], ...]:
        """Return the interpreters a POSIX host usually has, newest first, then this process's own.

        Returns:
            ``python3.13``, ``python3.12``, ``python3`` and, unless this is a frozen binary, the
            interpreter running the packet.
        """
        return (("python3.13",), ("python3.12",), ("python3",), *own_interpreter())

    def venv_executable(self, venv: Path, name: str) -> Path:
        """Return ``venv/bin/<name>``, where a POSIX virtual environment keeps its programs.

        Args:
            venv: The environment's directory.
            name: ``python`` or a console script.

        Returns:
            The path of that program.
        """
        return venv / "bin" / name

    def facts(self) -> PlatformFacts:
        """Probe the device for the enrolment report.

        Returns:
            What the operating system, the environment and the installed programs say.
        """
        has_display = bool(self._env.get("DISPLAY") or self._env.get("WAYLAND_DISPLAY"))
        return PlatformFacts(
            os_family=self._family(),
            distribution=self._distribution(),
            architecture=platform.machine() or "unknown",
            package_manager=next((m for m in self.package_managers if shutil.which(m)), None),
            shell=self._env.get("SHELL", "/bin/sh"),
            has_display=has_display,
            can_start_display=self.can_host_x11 and _all_on_path(_X11_PROGRAMS),
            has_audio=self.can_host_x11 and _all_on_path(_AUDIO_PROGRAMS),
            has_browser=any(shutil.which(name) for name in _BROWSERS) or self._app_browser(),
        )

    def _family(self) -> OsFamily:
        """Return the OS family; the subclass knows which it is."""
        raise NotImplementedError

    def _distribution(self) -> str | None:
        """Return the distribution or edition name; the subclass knows how to read it."""
        raise NotImplementedError

    def _app_browser(self) -> bool:
        """Return whether a browser is installed outside PATH (macOS applications)."""
        return False

    def _members(self, ref: ProcessRef) -> list[int]:
        """List the live processes that belong to ``ref``'s group and are not impostors."""
        members: list[int] = []
        for proc in psutil.process_iter(["pid", "create_time", "status"]):
            info = proc.info
            pid, created, status = info["pid"], info["create_time"], info["status"]
            if created is None or status == psutil.STATUS_ZOMBIE:
                continue  # Unreadable or already dead: not a live member.
            # The recycled-pid case: something else now holds the leader's pid.
            if pid == ref.pid and not _same_process(ref, created):
                continue
            # A member of the group born before the leader started cannot be its descendant.
            if ref.created and created < ref.created - CREATE_TIME_TOLERANCE_S:
                continue
            try:
                if os.getpgid(pid) == ref.pid:
                    members.append(pid)
            except OSError:
                continue  # Vanished mid-scan.
        return members


def _same_process(ref: ProcessRef, created: float) -> bool:
    """Return whether a process created at ``created`` is the one ``ref`` recorded."""
    # An unknown creation time (0.0) cannot be checked; every other must match closely.
    return ref.created == 0.0 or abs(created - ref.created) <= CREATE_TIME_TOLERANCE_S


def _all_on_path(programs: tuple[str, ...]) -> bool:
    """Return whether every one of ``programs`` resolves on PATH."""
    return all(shutil.which(program) for program in programs)
