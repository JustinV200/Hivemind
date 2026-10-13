"""Test the macOS shim: Application Support, the macOS version and a LaunchAgent.

On macOS the Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its files
under ``~/Library/Application Support/pollen`` and runs as a LaunchAgent in the user's
``~/Library/LaunchAgents``, loaded with ``launchctl``. Install writes the one property list and
loads it, or, where launchctl cannot load it, prints the command that starts the packet by hand;
uninstall unloads the agent and removes that one file. These tests run on any host: a recorder
stands in for ``launchctl`` and the platform version is scripted.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.macos.

Key invariants:
    - The property list is valid and runs the packet at load and again if it exits.
    - Uninstall removes only the file install wrote.

See Also:
    - docs/waggle/gateway-contract.md section 3.
"""

from __future__ import annotations

import plistlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from pollen.platform import CommandResult, MacOsShim, ServicePlan
from pollen.platform.macos import AGENT_LABEL


class Launchctl:
    """A recorder that answers every command with one scripted status."""

    def __init__(self, returncode: int = 0) -> None:
        """Answer each command with ``returncode``."""
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        """Record the command and answer it."""
        self.calls.append(tuple(argv))
        return CommandResult(self.returncode, "")


def test_the_data_directory_is_under_application_support(tmp_path: Path) -> None:
    shim = MacOsShim(tmp_path, {}, Launchctl())
    assert shim.default_data_dir() == tmp_path / "Library/Application Support/pollen"


def test_install_writes_a_valid_agent_and_loads_it(tmp_path: Path) -> None:
    runner = Launchctl()
    plan = ServicePlan(("/usr/bin/python3", "-m", "pollen", "run"), tmp_path / "data")
    result = MacOsShim(tmp_path, {}, runner).install_service(plan)
    assert result.is_active and result.unit_path is not None
    assert result.unit_path == tmp_path / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
    agent = plistlib.loads(result.unit_path.read_bytes())
    assert agent["Label"] == AGENT_LABEL
    assert agent["ProgramArguments"] == list(plan.command)
    assert (agent["RunAtLoad"], agent["KeepAlive"]) == (True, True)
    assert runner.calls == [("launchctl", "load", "-w", str(result.unit_path))]


def test_install_that_launchctl_cannot_load_prints_the_command(tmp_path: Path) -> None:
    plan = ServicePlan(("/usr/bin/python3", "-m", "pollen", "run"), tmp_path / "data")
    result = MacOsShim(tmp_path, {}, Launchctl(returncode=1)).install_service(plan)
    assert not result.is_active and result.manual_command == "/usr/bin/python3 -m pollen run"
    assert result.unit_path is not None and result.unit_path.exists()


def test_uninstall_unloads_the_agent_and_removes_only_its_file(tmp_path: Path) -> None:
    runner = Launchctl()
    shim = MacOsShim(tmp_path, {}, runner)
    installed = shim.install_service(ServicePlan(("python3",), tmp_path / "data"))
    assert installed.unit_path is not None
    neighbour = installed.unit_path.parent / "mine.plist"
    neighbour.write_bytes(plistlib.dumps({"Label": "mine"}))
    runner.calls.clear()
    removed = shim.uninstall_service()
    assert removed.unit_path is not None and not removed.unit_path.exists() and neighbour.exists()
    assert runner.calls == [("launchctl", "unload", "-w", str(installed.unit_path))]
    assert "no agent" in shim.uninstall_service().detail


def test_the_version_and_a_browser_are_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applications = tmp_path / "Applications"
    (applications / "Safari.app").mkdir(parents=True)
    shim = MacOsShim(tmp_path, {"SHELL": "/bin/zsh"}, Launchctl(), applications=applications)
    monkeypatch.setattr("platform.mac_ver", lambda: ("14.5", ("", "", ""), ""))
    facts = shim.facts()
    assert (facts.distribution, facts.has_browser, facts.shell) == ("macOS 14.5", True, "/bin/zsh")
    assert facts.os_family.value == "MACOS"
    monkeypatch.setattr("platform.mac_ver", lambda: ("", ("", "", ""), ""))
    assert shim.facts().distribution is None
