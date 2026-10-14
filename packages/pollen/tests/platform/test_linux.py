"""Test the Linux shim: the XDG data directory, os-release facts and the systemd user unit.

On Linux the Pollen Packet (the thin gateway program on a device the Hive borrows) keeps its files
under ``$XDG_DATA_HOME/pollen`` and runs as a systemd *user* unit that ``pollen install`` writes and
``pollen uninstall`` removes, and nothing else of the operator's is touched. Where there is no user
manager (a container, an SSH session without lingering) install still writes the unit and prints
the command that starts the packet by hand instead of failing. A recorder stands in for
``systemctl``, so these tests check the exact commands, the unit text and the files, and never
touch the host's service manager.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.linux.

Key invariants:
    - Install writes one file and uninstall removes that one file.
    - The unit never restarts the packet after an exit status that a restart cannot fix.
    - Stopping the packet's unit leaves the Warden it started running (``KillMode=process``).
    - ``ExecStart`` quotes by systemd's own rules, so a path with a space or a ``%`` survives.

See Also:
    - docs/waggle/gateway-contract.md section 3.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pollen.platform import EXIT_NO_RESTART, CommandResult, LinuxShim, ServicePlan
from pollen.platform.linux import UNIT_NAME


class Systemctl:
    """A recorder that answers each command with the status scripted for its subcommand."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        """Fail the subcommands named in ``failing`` (``daemon-reload``, ``enable``)."""
        self.failing = failing
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        """Record the command; fail it if its subcommand is scripted to fail."""
        self.calls.append(tuple(argv))
        return CommandResult(1 if argv[2] in self.failing else 0, "")


def _shim(tmp_path: Path, runner: Systemctl, env: dict[str, str] | None = None) -> LinuxShim:
    """Build a Linux shim over a throwaway home."""
    return LinuxShim(tmp_path / "home", env or {}, runner)


def _plan(tmp_path: Path, *command: str) -> ServicePlan:
    """Plan a service that runs ``command``."""
    return ServicePlan(command or ("/usr/bin/python3", "-m", "pollen", "run"), tmp_path / "data")


def test_the_data_directory_follows_xdg_then_falls_back_to_the_home(tmp_path: Path) -> None:
    runner = Systemctl()
    assert _shim(tmp_path, runner, {"XDG_DATA_HOME": str(tmp_path / "xdg")}).default_data_dir() == (
        tmp_path / "xdg" / "pollen"
    )
    assert _shim(tmp_path, runner).default_data_dir() == tmp_path / "home/.local/share/pollen"


def test_the_distribution_is_read_from_os_release(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n')
    shim = LinuxShim(tmp_path, {}, Systemctl(), os_release=release)
    assert shim.facts().distribution == "Ubuntu 24.04 LTS"
    plain = tmp_path / "plain"
    plain.write_text("NAME=Other\n")
    assert LinuxShim(tmp_path, {}, Systemctl(), os_release=plain).facts().distribution is None
    missing = LinuxShim(tmp_path, {}, Systemctl(), os_release=tmp_path / "absent")
    assert missing.facts().distribution is None


def test_install_writes_the_unit_reloads_enables_and_starts_it(tmp_path: Path) -> None:
    runner = Systemctl()
    result = _shim(tmp_path, runner).install_service(_plan(tmp_path))
    assert result.is_active and result.manual_command is None and result.unit_path is not None
    assert result.unit_path == tmp_path / "home/.config/systemd/user" / UNIT_NAME
    assert runner.calls == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", UNIT_NAME),
    ]
    text = result.unit_path.read_text()
    assert "ExecStart=/usr/bin/python3 -m pollen run\n" in text
    assert "Restart=on-failure\n" in text
    assert f"RestartPreventExitStatus={EXIT_NO_RESTART}\n" in text
    assert "WantedBy=default.target" in text


def test_the_unit_stops_only_the_packet_so_a_warden_it_started_keeps_running(
    tmp_path: Path,
) -> None:
    result = _shim(tmp_path, Systemctl()).install_service(_plan(tmp_path))

    assert result.unit_path is not None
    assert "KillMode=process\n" in result.unit_path.read_text()


def test_install_without_a_user_manager_writes_the_unit_and_prints_the_command(
    tmp_path: Path,
) -> None:
    result = _shim(tmp_path, Systemctl(frozenset({"daemon-reload"}))).install_service(
        _plan(tmp_path)
    )
    assert not result.is_active and result.unit_path is not None and result.unit_path.exists()
    assert result.manual_command == "/usr/bin/python3 -m pollen run"
    assert "by hand" in result.detail


def test_install_that_systemd_refuses_to_start_still_prints_the_command(tmp_path: Path) -> None:
    result = _shim(tmp_path, Systemctl(frozenset({"enable"}))).install_service(_plan(tmp_path))
    assert not result.is_active and result.manual_command is not None
    assert "refused" in result.detail


def test_the_execstart_line_quotes_by_systemds_rules(tmp_path: Path) -> None:
    command = ("/opt/my pollen/bin/python", "-m", "pollen", "--name", 'a"b', "100%", "$HOME")
    result = _shim(tmp_path, Systemctl()).install_service(_plan(tmp_path, *command))
    assert result.unit_path is not None
    line = next(x for x in result.unit_path.read_text().splitlines() if x.startswith("ExecStart="))
    assert line == (
        'ExecStart="/opt/my pollen/bin/python" -m pollen --name "a\\"b" "100%%" "$$HOME"'
    )


def test_uninstall_stops_the_unit_and_removes_only_that_file(tmp_path: Path) -> None:
    runner = Systemctl()
    shim = _shim(tmp_path, runner)
    installed = shim.install_service(_plan(tmp_path))
    neighbour = installed.unit_path.parent / "mine.service"  # type: ignore[union-attr]
    neighbour.write_text("[Unit]\n")
    runner.calls.clear()
    removed = shim.uninstall_service()
    assert removed.unit_path is not None and not removed.unit_path.exists()
    assert neighbour.exists()
    assert ("systemctl", "--user", "disable", "--now", UNIT_NAME) in runner.calls
    assert "removed" in removed.detail


def test_uninstalling_twice_is_harmless(tmp_path: Path) -> None:
    shim = _shim(tmp_path, Systemctl())
    shim.install_service(_plan(tmp_path))
    shim.uninstall_service()
    assert "no unit" in shim.uninstall_service().detail
