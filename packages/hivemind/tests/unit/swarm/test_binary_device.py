"""Unit: the clean device, the Hive a container dials and the scripted Drone, with no daemon.

The rig in `tests/e2e/swarm/binary` is proved whole by `tests/e2e/test_phase11_exit_binary.py`.
What can go wrong before a container exists is checked here in milliseconds on every run: how the
scripted Drone answers on both tool protocols, what the Hive's manifest says (the bridge address,
TLS, the name the device maps, a Drone that costs no cores), and how the clean device builds its
commands (the operator's account, standard input only when there is text, the snapshot, the stop).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises
    `e2e.swarm.binary.clean` and `.stand`; nothing here starts a container or touches a network.

Key invariants:
    - Docker is replaced by a recorder, so no test here depends on a daemon being present.
    - No test sleeps: a wait on the device is answered on its first look.

See Also:
    - tests/unit/swarm/test_binary_build.py for the cache key and the build.
    - tests/e2e/test_phase11_exit_binary.py for the rig run for real.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from builders.llm import make_request, make_tool
from e2e.swarm import container as container_module
from e2e.swarm.binary import clean as clean_module
from e2e.swarm.binary.clean import (
    CLEAN_IMAGE,
    EXIT_FILE,
    HARNESS_DIR,
    PID_FILE,
    CleanDevice,
    start_clean,
)
from e2e.swarm.binary.stand import (
    BEAT_S,
    INSPECT_FILE,
    REPORT_FILE,
    DeviceDrone,
    bridge_manifest,
)
from e2e.swarm.container import CommandError, CommandResult, Container, Janitor

from hivemind.llm import Message, Role, ToolCallPart, ToolResultPart
from hivemind.manifest import load_manifest


def _call_values(call: tuple[str, ...], flag: str) -> list[str]:
    """The argument after each occurrence of `flag` in a docker call."""
    return [call[i + 1] for i, word in enumerate(call) if word == flag]


# ──────────────────────────────────────────────────────────────────────────────
# The scripted Drone
# ──────────────────────────────────────────────────────────────────────────────


def _results(*texts: str) -> Message:
    """A user turn carrying one tool result for each of `texts`."""
    parts = tuple(ToolResultPart(call_id=f"c{i}", content=text) for i, text in enumerate(texts))
    return Message(role=Role.USER, parts=parts)


def _calls(response_parts: Sequence[object]) -> dict[str, dict[str, object]]:
    """The tool calls of a native response: name to arguments."""
    return {
        part.call.name: dict(part.call.arguments)
        for part in response_parts
        if isinstance(part, ToolCallPart)
    }


def test_the_drone_runs_a_command_and_writes_a_file_then_reads_the_commands_report() -> None:
    drone = DeviceDrone()
    tools = (make_tool(),)  # A non-empty tool list is what makes a request native.

    first = drone.turn(make_request(tools=tools))
    second = drone.turn(make_request(tools=tools, messages=(_results("verdict", "verdict"),)))

    started = _calls(first.parts)
    argv = started["run_command"]["argv"]
    assert isinstance(argv, list) and argv[:2] == ["sh", "-c"] and INSPECT_FILE in argv[2]
    assert started["write_file"]["path"] == REPORT_FILE
    assert _calls(second.parts) == {"read_file": {"path": INSPECT_FILE}}
    assert drone.printed == ""  # Nothing has been read back yet.


def test_the_drone_keeps_what_it_read_and_finishes() -> None:
    drone = DeviceDrone()
    results = _results("verdict", "verdict", "os=Debian GNU/Linux 12 (bookworm)")

    final = drone.turn(make_request(tools=(make_tool(),), messages=(results,)))

    assert _calls(final.parts) == {}  # No more tool calls: the Drone is done.
    assert "os=Debian GNU/Linux 12 (bookworm)" in drone.printed


def test_the_drone_answers_on_the_prompted_protocol_too() -> None:
    drone = DeviceDrone()

    first = drone.turn(make_request())  # No tools offered: calls are fenced blocks in text.

    text = "".join(getattr(part, "text", "") for part in first.parts)
    assert text.count("```tool") == 2 and '"run_command"' in text and '"write_file"' in text


# ──────────────────────────────────────────────────────────────────────────────
# The Hive a container dials
# ──────────────────────────────────────────────────────────────────────────────


def test_the_hives_manifest_listens_on_the_bridge_over_tls_and_advertises_the_name_the_device_maps(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(bridge_manifest(tmp_path, "127.0.0.1"), {})

    swarm = manifest.swarm
    assert (swarm.enabled, swarm.listen_host) == (True, "127.0.0.1")
    assert swarm.advertise_url == f"wss://hive.test:{swarm.listen_port}"
    # The invite pins this certificate, so it has to be the one the listener serves.
    assert Path(swarm.tls_cert).is_file() and Path(swarm.tls_key).is_file()
    assert swarm.heartbeat_interval_s == BEAT_S


def test_the_drone_costs_no_cores_so_the_hosts_load_cannot_keep_its_task_waiting(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(bridge_manifest(tmp_path, "127.0.0.1"), {})

    drone = manifest.forage.roles["drone"]

    assert drone.cpu_cores == 0.0  # No cores to find free: the one-minute load decides nothing.
    assert drone.memory_bytes > 0  # Its memory is still accounted for, as in every scenario.


# ──────────────────────────────────────────────────────────────────────────────
# The clean device
# ──────────────────────────────────────────────────────────────────────────────


class RecordingRun:
    """Stands in for `run`: records each program's argv and stdin, answers from a table."""

    def __init__(self, answers: dict[str, CommandResult] | None = None) -> None:
        """Answer a program whose last argument is a key of `answers`; success otherwise."""
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self._answers = answers or {}

    def __call__(
        self,
        argv: Sequence[str],
        timeout_s: float,
        *,
        cwd: Path | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        """Record the call and answer it."""
        self.calls.append((tuple(argv), stdin))
        return self._answers.get(argv[-1], CommandResult(tuple(argv), 0, "", ""))


class ExitFile:
    """Stands in for `docker` where `Container.exists` asks if the packet's exit file is there."""

    def __init__(self, present_from: int) -> None:
        """Say "absent" for the first `present_from - 1` asks and "present" from then on."""
        self.asked = 0
        self._present_from = present_from

    def __call__(self, *args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        """Answer `test -e FILE`: exit 0 when the file exists, 1 when it does not."""
        self.asked += 1
        return CommandResult(
            ("docker", *args), 0 if self.asked >= self._present_from else 1, "", ""
        )


def test_a_command_gets_an_open_standard_input_only_when_it_is_given_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = RecordingRun()
    monkeypatch.setattr(clean_module, "run", recording)
    clean = CleanDevice(Container("clean-1"))

    clean.exec(["pollen", "version"])
    clean.exec(["pollen", "enrol", "-"], stdin="pollen1.secret")

    (plain, plain_stdin), (piped, piped_stdin) = recording.calls
    as_pi = ("docker", "exec", "--user", "pi")  # An operator is a person, not root.
    assert plain == (*as_pi, "clean-1", "pollen", "version") and plain_stdin is None
    assert piped == (*as_pi, "-i", "clean-1", "pollen", "enrol", "-")
    assert piped_stdin == "pollen1.secret"  # In the pipe, never in the argument list.


def test_a_failing_command_raises_unless_the_caller_wants_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = RecordingRun({"x": CommandResult(("docker",), 3, "", "no")})
    monkeypatch.setattr(clean_module, "run", failing)
    clean = CleanDevice(Container("clean-1"))

    with pytest.raises(CommandError):
        clean.exec(["x"])

    assert clean.exec(["x"], check=False).exit_code == 3


def test_the_snapshot_lists_files_and_links_on_the_devices_own_filesystem_less_the_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = "f 644 12 /etc/os-release\nl 777 9 /bin/sh\n"
    recording = RecordingRun({r"%y %m %s %p\n": CommandResult((), 0, listing, "")})
    monkeypatch.setattr(clean_module, "run", recording)

    entries = CleanDevice(Container("clean-1")).files()

    assert sorted(entries) == ["/bin/sh", "/etc/os-release"]
    [(find, _)] = recording.calls
    assert find == (
        *("docker", "exec", "--user", "root", "clean-1", "find", "/", "-xdev"),
        *("-path", HARNESS_DIR, "-prune", "-o", "!", "-type", "d", "-printf", r"%y %m %s %p\n"),
    )  # As root, to see everything; not /proc or /dev; not the harness's files; no directories.


def test_stopping_a_packet_that_already_ended_sends_it_no_signal_and_reports_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = RecordingRun({EXIT_FILE: CommandResult((), 0, "0\n", "")})
    monkeypatch.setattr(clean_module, "run", recording)
    monkeypatch.setattr(container_module, "docker", ExitFile(present_from=1))

    status = CleanDevice(Container("clean-1")).stop(timeout_s=1.0)

    assert status == 0
    assert [argv[-2:] for argv, _ in recording.calls] == [("cat", EXIT_FILE)]  # Only its status.


def test_stopping_a_running_packet_sends_it_sigterm_by_the_pid_the_supervisor_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = RecordingRun({EXIT_FILE: CommandResult((), 0, "0\n", "")})
    monkeypatch.setattr(clean_module, "run", recording)
    monkeypatch.setattr(container_module, "docker", ExitFile(present_from=2))

    status = CleanDevice(Container("clean-1")).stop(timeout_s=1.0)

    signal, _ = recording.calls[0]
    kill = 'kill -TERM "$(cat "$1")"'
    assert signal == ("docker", "exec", "--user", "pi", "clean-1", "sh", "-c", kill, "sh", PID_FILE)
    assert status == 0


def test_a_clean_device_has_the_operators_account_and_a_directory_the_account_can_write_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def record(*args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        calls.append(args)
        return CommandResult(("docker", *args), 0, "", "")

    monkeypatch.setattr(container_module, "docker", record)
    janitor = Janitor()

    device = start_clean(janitor, {"hive.test": "172.17.0.1"})
    janitor.close()

    run = calls[0]
    assert CLEAN_IMAGE in run and _call_values(run, "--add-host") == ["hive.test:172.17.0.1"]
    # Root creates the account and the harness's directory (sticky and open, like /tmp), once.
    assert ("exec", device.name, "useradd", "--create-home", "--shell", "/bin/sh", "pi") in calls
    assert ("exec", device.name, "mkdir", "--mode", "1777", HARNESS_DIR) in calls
