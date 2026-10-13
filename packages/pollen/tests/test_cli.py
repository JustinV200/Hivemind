"""Test the command line: the verbs, their exit statuses and what they print.

``pollen`` is what an operator types on a device the Hive will borrow (cli.py). ``enrol`` turns an
invite into an enrolment, ``run`` is the packet until a signal stops it, ``install`` and
``uninstall`` manage the service unit and nothing else of the operator's, ``status`` says what the
packet is doing and ``version`` prints the versions. Exit status 0 means done, 1 failed, and 78
(``EXIT_NO_RESTART``) means the packet stopped for a reason a restart cannot fix, so the service
manager must not restart it. These tests call ``main`` the way a shell would, with a recorder in
place of the service manager and the scripted Queen for the verbs that need one.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.cli and pollen.__main__.

Key invariants:
    - Nothing printed carries the invite, a token or a key.
    - The data directory survives ``uninstall``.
    - A signal handler the CLI installs is the process's old one again when ``run`` returns.

See Also:
    - docs/waggle/enrolment.md for the ceremony behind ``enrol``.
"""

from __future__ import annotations

import asyncio
import io
import os
import runpy
import signal
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from device_kit import Device
from queen_double import INVITE_CODE, QueenDouble
from states import sample_state

from pollen import build_packet
from pollen.agent import PacketStatus, StatusDocument, write_status
from pollen.cli import _run_until_signalled, data_dir_for, main, status_lines
from pollen.errors import ProtocolMismatchError
from pollen.platform import EXIT_NO_RESTART, CommandResult, LinuxShim, select_shim
from pollen.state import DataLayout, load_state, save_state
from waggle.clock import Clock


class Recorder:
    """A service manager that records every command and answers with a fixed status."""

    def __init__(self, returncode: int = 0) -> None:
        """Answer every command with ``returncode``."""
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        """Record one command and answer it."""
        self.calls.append(tuple(argv))
        return CommandResult(self.returncode, "")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Recorder:
    """Replace the platform shim with a Linux one that talks to a recorder, not systemd."""
    recorded = Recorder()
    shim = LinuxShim(tmp_path / "home", {}, recorded)
    monkeypatch.setattr("pollen.cli.select_shim", lambda: shim)
    return recorded


def test_version_prints_the_packet_and_protocol_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("pollen ")


def test_the_data_directory_comes_from_the_option_then_the_variable_then_the_platform(
    tmp_path: Path,
) -> None:
    shim = select_shim()
    option, variable = tmp_path / "option", tmp_path / "variable"
    env = {"POLLEN_DATA_DIR": str(variable)}
    assert data_dir_for(str(option), shim, env) == option
    assert data_dir_for(None, shim, env) == variable
    assert data_dir_for(None, shim, {}) == shim.default_data_dir().resolve()


def test_status_says_not_running_without_a_recent_document(device: Device, clock: Clock) -> None:
    lines = status_lines(device.state, None, clock.now())
    assert lines[0] == f"device      {device.state.device_id}"
    assert lines[-1].startswith("state       not running")


def _document(clock: Clock, **changes: object) -> StatusDocument:
    """Build a status document of this very process, as a running packet would write."""
    status = PacketStatus(clock)
    status.connected()
    base = status.snapshot(open_leases=2, outbox_pending=1, silence_s=3.0)
    return base.model_copy(update=changes)


def test_status_shows_the_link_the_leases_and_the_silence(device: Device, clock: Clock) -> None:
    text = "\n".join(status_lines(device.state, _document(clock), clock.now()))
    assert "running, connected" in text and "2 open, 1 frames queued" in text
    assert "3 s since the Queen was last heard" in text and "warning" not in text


def test_status_names_the_last_error_and_warns_about_the_clock(
    device: Device, clock: Clock
) -> None:
    down = _document(
        clock, is_connected=False, last_failure="waggle.transport.tls_pin", is_clock_suspect=True
    )
    text = "\n".join(status_lines(device.state, down, clock.now()))
    assert (
        "not connected" in text and "waggle.transport.tls_pin" in text and "check the clock" in text
    )


def test_status_does_not_trust_an_old_document_or_a_dead_process(
    device: Device, clock: Clock
) -> None:
    old = _document(clock)
    later = clock.now() + timedelta(hours=1)
    assert "not running (no recent status)" in "\n".join(status_lines(device.state, old, later))
    gone = _document(clock, pid=2**22 + 123)
    assert "the process has gone" in "\n".join(status_lines(device.state, gone, clock.now()))


def test_status_for_an_unenrolled_directory_fails_with_a_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["status", "--data-dir", str(tmp_path / "empty")]) == 1
    assert "not enrolled" in capsys.readouterr().err


async def test_status_prints_an_enrolled_devices_summary(
    device: Device, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    await write_status(device.layout.status, _document(clock))
    assert await asyncio.to_thread(main, ["status", "--data-dir", str(device.layout.root)]) == 0
    assert "running, connected" in capsys.readouterr().out


async def test_enrol_turns_an_invite_into_an_enrolment_and_prints_no_secret(
    queen: QueenDouble, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "device"
    args = ["enrol", queen.invite(), "--data-dir", str(root)]
    assert await asyncio.to_thread(main, args) == 0
    captured = capsys.readouterr()
    state = load_state(DataLayout(root).state)
    assert state.device_id in captured.out and "pollen install" in captured.out
    assert INVITE_CODE not in captured.out + captured.err and "pollen1." not in captured.out


async def test_enrol_reads_the_invite_from_standard_input_when_given_a_dash(
    queen: QueenDouble, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(queen.invite() + "\n"))
    root = tmp_path / "device"
    assert await asyncio.to_thread(main, ["enrol", "-", "--data-dir", str(root)]) == 0
    assert DataLayout(root).state.exists()


async def test_enrol_refuses_a_bad_invite_and_a_denial_with_exit_status_one(
    queen: QueenDouble, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(tmp_path / "device")
    assert await asyncio.to_thread(main, ["enrol", "not-an-invite", "--data-dir", root]) == 1
    assert "pollen1" in capsys.readouterr().err
    queen.decide = queen.deny
    assert await asyncio.to_thread(main, ["enrol", queen.invite(), "--data-dir", root]) == 1
    assert "refused" in capsys.readouterr().err


def test_run_without_an_enrolment_stops_for_good(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--data-dir", str(tmp_path / "empty")]) == EXIT_NO_RESTART
    assert "not enrolled" in capsys.readouterr().err


async def test_a_signal_stops_the_packet_and_the_old_handlers_come_back(
    queen: QueenDouble, device: Device, clock: Clock
) -> None:
    before = signal.getsignal(signal.SIGTERM)
    packet = build_packet(device.layout, select_shim(), clock)
    stopped = asyncio.create_task(_run_until_signalled(packet))
    await queen.next_session()
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(stopped, timeout=20)
    assert signal.getsignal(signal.SIGTERM) == before


def test_a_protocol_mismatch_is_exit_status_78(
    monkeypatch: pytest.MonkeyPatch, device: Device, capsys: pytest.CaptureFixture[str]
) -> None:
    class Mismatched:
        async def run(self) -> None:
            raise ProtocolMismatchError("The Queen speaks a Waggle major this packet does not.")

        def stop(self) -> None:
            return None

    monkeypatch.setattr("pollen.cli.build_packet", lambda *_: Mismatched())
    assert main(["run", "--data-dir", str(device.layout.root)]) == EXIT_NO_RESTART
    assert "major" in capsys.readouterr().err


def test_install_writes_the_unit_and_records_the_operators_runtime_source(
    recorder: Recorder, tmp_path: Path, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = DataLayout(tmp_path / "data")
    layout.ensure()
    save_state(layout.state, sample_state(clock))
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    manifest = tmp_path / "warden.toml"
    manifest.write_text("[hive]\n")
    args = ["install", "--data-dir", str(layout.root), "--runtime-wheelhouse", str(wheels)]
    assert main([*args, "--warden-manifest", str(manifest)]) == 0
    recorded = load_state(layout.state).install
    assert recorded.runtime_wheelhouse == str(wheels.resolve())
    assert recorded.warden_manifest == str(manifest.resolve())
    assert ("systemctl", "--user", "enable", "--now", "pollen.service") in recorder.calls
    assert "installed" in capsys.readouterr().out


def test_install_without_a_service_manager_prints_the_command_to_start_it(
    recorder: Recorder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder.returncode = 1
    assert main(["install", "--data-dir", str(tmp_path / "data")]) == 0
    out = capsys.readouterr().out
    assert "run --data-dir" in out and "not enrolled yet" in out


def test_install_with_settings_needs_an_enrolment_to_record_them(
    recorder: Recorder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [
        "install",
        "--data-dir",
        str(tmp_path / "data"),
        "--runtime-index",
        "https://x.example/simple",
    ]
    assert main(args) == 1
    assert "not enrolled" in capsys.readouterr().err
    assert recorder.calls == []


def test_uninstall_removes_the_unit_and_leaves_the_data(
    recorder: Recorder, tmp_path: Path, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = DataLayout(tmp_path / "data")
    layout.ensure()
    save_state(layout.state, sample_state(clock))
    assert main(["install", "--data-dir", str(layout.root)]) == 0
    assert main(["uninstall", "--data-dir", str(layout.root)]) == 0
    assert layout.state.exists()
    assert "left in place" in capsys.readouterr().out
    assert ("systemctl", "--user", "disable", "--now", "pollen.service") in recorder.calls


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["frobnicate"],
        ["install", "--runtime-wheelhouse", "/definitely/not/here"],
        ["install", "--runtime-index", "ftp://nope"],
        ["install", "--warden-manifest", "/definitely/not/here.toml"],
        ["install", "--runtime-index", "https://x.example", "--runtime-wheelhouse", "."],
        ["enrol"],
    ],
)
def test_a_malformed_command_line_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def test_python_dash_m_pollen_runs_the_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["pollen", "version"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("pollen", run_name="__main__")
    assert raised.value.code == 0 and capsys.readouterr().out.startswith("pollen ")
