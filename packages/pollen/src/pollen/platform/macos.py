"""Provide MacOsShim: Application Support paths, the macOS version and a LaunchAgent.

The Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its data under
``~/Library/Application Support`` on a Mac, reports the macOS version as its distribution (a Real
Cell is an existing device the Hive borrows for a task, and placement reads this report), and is
installed as a LaunchAgent in the user's own ``~/Library/LaunchAgents`` so it starts at login
with no administrator rights. The property list is written with the standard library's
``plistlib``; loading it goes through ``launchctl``, and where that fails the install still
leaves the agent in place and prints the command that starts the packet by hand.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Chosen by
    pollen.platform.select on macOS; called by pollen.cli through the PlatformShim Protocol;
    calls into pollen.platform.posix and the standard library only.

Key invariants:
    - The agent is one file in the user's LaunchAgents directory; uninstalling unloads and
      removes that file and nothing of the operator's, the data directory included.
    - Every launchctl command goes through the injected runner.

See Also:
    - docs/waggle/gateway-contract.md section 3 for what a gateway needs from its platform.
    - pollen.platform.posix for the process-group behaviour this shim inherits.
"""

from __future__ import annotations

import platform
import plistlib
import shlex
from collections.abc import Mapping
from pathlib import Path

from pollen.platform.base import CommandRunner, ServicePlan, ServiceResult, run_command
from pollen.platform.posix import PosixShim
from waggle.messages.labels import OsFamily

AGENT_LABEL = "io.hivemind.pollen"  # The LaunchAgent's label and the stem of its file name.
_APP_BROWSERS = ("Safari.app", "Google Chrome.app", "Firefox.app")  # Under /Applications.

__all__ = ["AGENT_LABEL", "MacOsShim"]


class MacOsShim(PosixShim):
    """The macOS platform: Application Support, the macOS version and a LaunchAgent."""

    name = "macos"
    package_managers = ("brew", "port")

    def __init__(
        self,
        home: Path,
        env: Mapping[str, str] | None = None,
        runner: CommandRunner = run_command,
        applications: Path = Path("/Applications"),
    ) -> None:
        """Bind the shim to a home directory, environment and the applications directory.

        Args:
            home: The account's home directory.
            env: The environment to read SHELL from; empty when None.
            runner: Runs launchctl; tests pass a recorder.
            applications: Where installed applications live, to find a browser.
        """
        super().__init__(home, {} if env is None else env, runner)
        self._applications = applications

    def default_data_dir(self) -> Path:
        """Return ``~/Library/Application Support/pollen``.

        Returns:
            The conventional per-user data directory.
        """
        return self._home / "Library" / "Application Support" / "pollen"

    def install_service(self, plan: ServicePlan) -> ServiceResult:
        """Write the LaunchAgent and load it with launchctl.

        Args:
            plan: The command to run and the data directory it uses.

        Returns:
            The agent's path; ``is_active`` true when launchctl loaded it, else the command that
            starts the packet by hand.
        """
        agent = self._agent_path()
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(_agent_plist(plan))
        loaded = self._runner(["launchctl", "load", "-w", str(agent)])
        if loaded.returncode != 0:
            return ServiceResult(
                unit_path=agent,
                is_active=False,
                manual_command=shlex.join(plan.command),
                detail="The agent was written, but launchctl could not load it here; start the "
                "packet by hand with the command shown.",
            )
        return ServiceResult(agent, True, None, "The packet is installed and running as an agent.")

    def uninstall_service(self) -> ServiceResult:
        """Unload the agent, then remove the one file ``install_service`` wrote.

        Returns:
            The agent's path and whether it was there.
        """
        agent = self._agent_path()
        existed = agent.exists()
        if existed:
            self._runner(["launchctl", "unload", "-w", str(agent)])
        agent.unlink(missing_ok=True)
        detail = "The agent was removed." if existed else "There was no agent to remove."
        return ServiceResult(unit_path=agent, is_active=False, manual_command=None, detail=detail)

    def _agent_path(self) -> Path:
        """Return the agent's property list path in the user's LaunchAgents directory."""
        return self._home / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"

    def _family(self) -> OsFamily:
        """Return MACOS."""
        return OsFamily.MACOS

    def _distribution(self) -> str | None:
        """Return ``macOS <version>``; None when the version cannot be read."""
        version = platform.mac_ver()[0]
        return f"macOS {version}" if version else None

    def _app_browser(self) -> bool:
        """Return whether Safari, Chrome or Firefox is installed as an application."""
        return any((self._applications / name).exists() for name in _APP_BROWSERS)


def _agent_plist(plan: ServicePlan) -> bytes:
    """Render the LaunchAgent property list: run at load, restart if it exits."""
    return plistlib.dumps(
        {
            "Label": AGENT_LABEL,
            "ProgramArguments": list(plan.command),
            "RunAtLoad": True,
            "KeepAlive": True,
            "WorkingDirectory": str(plan.data_dir),
        }
    )
