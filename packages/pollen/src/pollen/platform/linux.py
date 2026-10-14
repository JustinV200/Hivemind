"""Provide LinuxShim: XDG paths, the distribution name and a systemd user unit for the packet.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its data under
the XDG data directory, reports the distribution it runs on (a Real Cell is an existing device
the Hive borrows for a task, and placement reads this report), and is installed as a systemd
*user* unit so it starts when the operator logs in and needs no root. Any distribution works
through generic systemd and XDG paths. Where systemd is absent, or has no user session (a
container, a minimal chroot), installation still writes the unit and then prints the command that
starts the packet by hand instead of failing: the unit is the durable half of the install, and
starting it is something the operator can always do.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Chosen by
    pollen.platform.select on Linux and other POSIX systems; called by pollen.cli through the
    PlatformShim Protocol; calls into pollen.platform.posix and the standard library only.

Key invariants:
    - The unit lives in the user's own systemd directory; uninstalling removes that one file and
      nothing of the operator's, the data directory included.
    - Every systemd command goes through the injected runner, so a missing manager is a result
      and never an exception.

See Also:
    - docs/waggle/gateway-contract.md section 3 for what a gateway needs from its platform.
    - pollen.platform.posix for the process-group behaviour this shim inherits.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import Path

from pollen.platform.base import (
    EXIT_NO_RESTART,
    CommandRunner,
    ServicePlan,
    ServiceResult,
    run_command,
)
from pollen.platform.posix import PosixShim
from waggle.messages.labels import OsFamily

UNIT_NAME = "pollen.service"  # The user unit's file name and the name systemctl is given.
OS_RELEASE = Path("/etc/os-release")  # The freedesktop file every mainstream distribution ships.
_SAFE_WORD = re.compile(r"[A-Za-z0-9_@+=:,./-]+")  # What systemd reads as one word, unquoted.

__all__ = ["OS_RELEASE", "UNIT_NAME", "LinuxShim"]


class LinuxShim(PosixShim):
    """The Linux platform: XDG data directory, os-release facts and a systemd user unit."""

    name = "linux"
    package_managers = ("apt", "dnf", "yum", "zypper", "pacman", "apk")
    can_host_x11 = True

    def __init__(
        self,
        home: Path,
        env: Mapping[str, str] | None = None,
        runner: CommandRunner = run_command,
        os_release: Path = OS_RELEASE,
    ) -> None:
        """Bind the shim to a home directory, environment and the os-release file.

        Args:
            home: The account's home directory.
            env: The environment to read XDG_*, SHELL and DISPLAY from; empty when None.
            runner: Runs systemctl; tests pass a recorder.
            os_release: Where the distribution name is read from.
        """
        super().__init__(home, {} if env is None else env, runner)
        self._os_release = os_release

    def default_data_dir(self) -> Path:
        """Return ``$XDG_DATA_HOME/pollen``, falling back to ``~/.local/share/pollen``.

        Returns:
            The conventional per-user data directory.
        """
        base = self._env.get("XDG_DATA_HOME") or str(self._home / ".local" / "share")
        return Path(base) / "pollen"

    def install_service(self, plan: ServicePlan) -> ServiceResult:
        """Write the systemd user unit and start it where a user manager exists.

        Args:
            plan: The command to run and the data directory it uses.

        Returns:
            The unit's path; ``is_active`` true when systemd enabled and started it, else the
            command that starts the packet by hand.
        """
        unit = self._unit_path()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_unit_text(plan), encoding="utf-8")
        by_hand = shlex.join(plan.command)
        # daemon-reload doubles as the probe: it fails the same way wherever no user manager runs.
        if self._runner(["systemctl", "--user", "daemon-reload"]).returncode != 0:
            return ServiceResult(
                unit_path=unit,
                is_active=False,
                manual_command=by_hand,
                detail="The unit was written, but systemd has no user session here; start the "
                "packet by hand with the command shown.",
            )
        started = self._runner(["systemctl", "--user", "enable", "--now", UNIT_NAME])
        if started.returncode != 0:
            return ServiceResult(
                unit_path=unit,
                is_active=False,
                manual_command=by_hand,
                detail="The unit was written, but systemd refused to start it; start the packet "
                "by hand with the command shown.",
            )
        return ServiceResult(
            unit, True, None, "The packet is installed and running as a user unit."
        )

    def uninstall_service(self) -> ServiceResult:
        """Stop and disable the unit, then remove the one file ``install_service`` wrote.

        Returns:
            The unit's path and whether it was there.
        """
        unit = self._unit_path()
        # Best effort: a manager that is absent has nothing to stop, and the file goes regardless.
        self._runner(["systemctl", "--user", "disable", "--now", UNIT_NAME])
        existed = unit.exists()
        unit.unlink(missing_ok=True)
        if existed:
            self._runner(["systemctl", "--user", "daemon-reload"])
        detail = "The unit was removed." if existed else "There was no unit to remove."
        return ServiceResult(unit_path=unit, is_active=False, manual_command=None, detail=detail)

    def _unit_path(self) -> Path:
        """Return the user unit's path under ``$XDG_CONFIG_HOME/systemd/user``."""
        base = self._env.get("XDG_CONFIG_HOME") or str(self._home / ".config")
        return Path(base) / "systemd" / "user" / UNIT_NAME

    def _family(self) -> OsFamily:
        """Return LINUX."""
        return OsFamily.LINUX

    def _distribution(self) -> str | None:
        """Read PRETTY_NAME from os-release; None when the file is missing or has none."""
        try:
            text = self._os_release.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key == "PRETTY_NAME":
                return value.strip().strip('"')[:64] or None
        return None


def _unit_text(plan: ServicePlan) -> str:
    """Render the unit file for ``plan``: restart on failure, start at login."""
    exec_start = " ".join(_systemd_word(part) for part in plan.command)
    return (
        "[Unit]\n"
        "Description=Pollen Packet, the HiveMind device gateway\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        # Not enrolled, or a Queen that speaks another protocol major: a restart cannot help.
        f"RestartPreventExitStatus={EXIT_NO_RESTART}\n"
        # The on-device Warden (ADR-0052) is a child of the packet, kept alive on purpose: a
        # restarted packet finds it again. systemd would otherwise kill the whole control group when
        # the packet stops or crashes, taking the Warden and its sub-bees' work with it.
        "KillMode=process\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemd_word(word: str) -> str:
    """Quote ``word`` for an ExecStart line: systemd's own rules, not a shell's."""
    if _SAFE_WORD.fullmatch(word) and "%" not in word:
        return word
    # Backslash and quote escape themselves; % and $ would otherwise be expanded by systemd.
    escaped = word.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'
