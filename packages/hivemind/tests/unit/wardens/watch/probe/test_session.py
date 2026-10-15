"""Tests for hivemind.wardens.watch.probe.session: SessionWatchProbe over a scripted machine.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/probe/session.py (codingrules section 3). The session is a
    `RecordingSession` over a `ScriptedMachine` (builders.watch) that answers the two fixed scripts
    the way a Linux box would, so the whole probe runs for real: the scripts are built, run through
    `hivemind.cell.run`, split, parsed and assembled into an Observation. What the session was
    asked is recorded, so a test can prove the probe only ever runs commands.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.probe.session for the module under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.watch import (
    DEVICE_ID,
    FakeProcess,
    RecordingSession,
    ScriptedMachine,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.cell import CommandTimeoutError, CompletedCommand, ExecSpec, SessionClosedError
from hivemind.wardens.watch.probe import PROBE_FAILURES, SessionWatchProbe
from waggle.clock import FakeClock
from waggle.messages.swarm import FileChangeKind, Observation, ObservationKind

_ALL = list(ObservationKind)
_ROOT = "/data/logs"
_LINUX = "linux"  # What a ScriptedMachine answers as, whatever the host running the tests is.
_NOW = FakeClock().now().timestamp()  # What the probe's clock reads, as the machine's epoch.
_BOOT = _NOW - 1_000.0  # The machine is 1,000 s old, so its uptime of 1,000 s matches.


def _rig(
    machine: ScriptedMachine | None = None, *, platform: str = _LINUX, max_items: int = 200
) -> tuple[SessionWatchProbe, RecordingSession, ScriptedMachine]:
    """A probe over a recording session over a machine; the session is already lent."""
    machine = machine if machine is not None else ScriptedMachine(boot_epoch=_BOOT)
    clock = FakeClock()
    session = RecordingSession(Path("/scratch"), clock, machine)
    probe = SessionWatchProbe(DEVICE_ID, clock, platform, max_items)
    probe.use_session(session)
    return probe, session, machine


def _busy_machine() -> ScriptedMachine:
    """A machine with three processes, two files under a root and one log."""
    machine = ScriptedMachine(boot_epoch=_BOOT, uptime_s=1_000.0)
    machine.processes = [
        FakeProcess(1, "init", utime=100, started_s=1.0),
        FakeProcess(2, "worker", utime=40_000, started_s=10.0, rss_pages=500),
        FakeProcess(3, "idle", utime=1, started_s=900.0),
    ]
    machine.files = {
        _ROOT: [(f"{_ROOT}/app.log", _NOW - 10.0, 40), (f"{_ROOT}/db", _NOW - 3_600.0, 8)]
    }
    machine.logs = {f"{_ROOT}/app.log": "started\nlistening on 80\n"}
    return machine


# ──────────────────────────────────────────────────────────────────────────────
# What it observes
# ──────────────────────────────────────────────────────────────────────────────


async def test_observe_reports_processes_resources_logs_and_file_changes() -> None:
    probe, _session, _machine = _rig(_busy_machine())

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert observation.device_id == DEVICE_ID
    assert [p.name for p in observation.processes] == ["worker", "init", "idle"]  # Busiest first.
    assert observation.processes[0].rss_bytes == 500 * 4096
    assert observation.resources is not None
    assert observation.resources.cpu_load == pytest.approx(0.8 / 4)
    assert [network.interface for network in observation.resources.network] == ["eth0"]
    assert [(line.path, line.line) for line in observation.log_lines] == [
        (f"{_ROOT}/app.log", "started"),
        (f"{_ROOT}/app.log", "listening on 80"),
    ]
    assert [c.path for c in observation.file_changes] == [f"{_ROOT}/app.log"]  # Recent only.
    assert observation.is_truncated is False
    assert observation.reason == "Observed."


async def test_the_observation_round_trips_through_the_wire_model() -> None:
    probe, _session, _machine = _rig(_busy_machine())

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert Observation.model_validate_json(observation.model_dump_json()) == observation


async def test_a_kind_that_was_not_asked_for_is_not_read() -> None:
    probe, session, _machine = _rig(_busy_machine())

    observation = await probe.observe([ObservationKind.RESOURCE_USE], [], None)

    assert observation.processes == ()
    assert observation.log_lines == () and observation.file_changes == ()
    assert observation.resources is not None
    (argv,) = session.execs
    assert "find" not in argv[2]
    assert "[0-9]*/stat" not in argv[2]


async def test_cpu_share_is_measured_over_the_interval_between_two_samples() -> None:
    machine = _busy_machine()
    probe, _session, _ = _rig(machine)
    await probe.observe([ObservationKind.PROCESS_LIST], [], None)

    # Ten seconds on: "worker" used 20 more CPU seconds, two of the four cores.
    machine.uptime_s += 10
    machine.processes[1].utime += 2_000
    observation = await probe.observe([ObservationKind.PROCESS_LIST], [], None)

    assert observation.processes[0].name == "worker"
    assert observation.processes[0].cpu_share == pytest.approx(0.5)


async def test_file_changes_follow_the_machine_between_samples() -> None:
    machine = _busy_machine()
    probe, _session, _ = _rig(machine)
    await probe.observe([ObservationKind.FILE_CHANGES], [_ROOT], None)

    machine.files[_ROOT] = [
        (f"{_ROOT}/app.log", _NOW - 5.0, 90),  # Modified.
        (f"{_ROOT}/new", _NOW - 4.0, 3),  # Created.
    ]  # db is gone.
    observation = await probe.observe([ObservationKind.FILE_CHANGES], [_ROOT], None)

    found = {c.path: c.change for c in observation.file_changes}
    assert found == {
        f"{_ROOT}/app.log": FileChangeKind.MODIFIED,
        f"{_ROOT}/new": FileChangeKind.CREATED,
        f"{_ROOT}/db": FileChangeKind.DELETED,
    }


async def test_max_items_caps_processes_and_file_changes() -> None:
    machine = ScriptedMachine(boot_epoch=_BOOT, uptime_s=1_000.0)
    machine.processes = [FakeProcess(n, f"p{n}", utime=n) for n in range(1, 8)]
    machine.files = {_ROOT: [(f"{_ROOT}/f{n}", _NOW - 100.0 + n, 1) for n in range(7)]}
    probe, _session, _ = _rig(machine, max_items=3)

    observation = await probe.observe(
        [ObservationKind.PROCESS_LIST, ObservationKind.FILE_CHANGES], [_ROOT], None
    )

    assert len(observation.processes) == len(observation.file_changes) == 3
    assert observation.is_truncated is True


async def test_a_part_the_machine_would_not_give_is_named_in_the_reason() -> None:
    machine = _busy_machine()
    machine.unreadable_sections = {"meminfo", "df"}
    probe, _session, _ = _rig(machine)

    observation = await probe.observe(
        [ObservationKind.RESOURCE_USE, ObservationKind.PROCESS_LIST], [], None
    )

    assert observation.resources is None
    assert observation.processes != ()
    assert observation.reason == "Not collected: RESOURCE_USE."


async def test_a_machine_that_prints_nothing_gives_an_empty_observation_naming_everything() -> None:
    machine = ScriptedMachine(unreadable_sections={"stat", "uptime", "procs", "meminfo", "df"})
    probe, _session, _ = _rig(machine)

    observation = await probe.observe(
        [ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE], [], None
    )

    assert (observation.processes, observation.resources) == ((), None)
    assert observation.reason == "Not collected: PROCESS_LIST, RESOURCE_USE."


async def test_files_without_a_usable_root_are_not_collected_and_say_so() -> None:
    probe, session, _ = _rig(_busy_machine())

    observation = await probe.observe(
        [ObservationKind.LOGS, ObservationKind.FILE_CHANGES], ["relative/dir", "/nul\0byte"], None
    )

    assert observation.log_lines == () and observation.file_changes == ()
    assert observation.reason == "Not collected: LOGS, FILE_CHANGES."
    assert "relative/dir" not in str(session.execs)


# ──────────────────────────────────────────────────────────────────────────────
# What it will and will not run
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_probe_only_ever_runs_commands_on_the_session() -> None:
    probe, session, _ = _rig(_busy_machine())

    await probe.observe(_ALL, [_ROOT], None)
    await probe.observe(_ALL, [_ROOT], None)

    assert set(session.calls) == {"exec"}  # Never a put_file, delete_file, start or stop.


async def test_one_observation_runs_two_commands_at_most_and_one_when_it_reads_no_log() -> None:
    probe, session, _ = _rig(_busy_machine())

    await probe.observe(_ALL, [_ROOT], None)
    assert len(session.execs) == 2  # The system script, then the tail of the log it listed.

    await probe.observe([ObservationKind.PROCESS_LIST, ObservationKind.FILE_CHANGES], [_ROOT], None)
    await probe.observe([ObservationKind.LOGS], [_ROOT], None)
    assert len(session.execs) == 5  # One, then two more (the log is still there to tail).

    session.execs.clear()
    quiet = ScriptedMachine(boot_epoch=_BOOT)
    quiet_probe, quiet_session, _ = _rig(quiet)
    await quiet_probe.observe([ObservationKind.LOGS], [_ROOT], None)
    assert len(quiet_session.execs) == 1  # No log file to tail: no second command.


async def test_every_command_is_a_shell_script_with_the_roots_as_plain_arguments() -> None:
    probe, session, _ = _rig(_busy_machine())
    hostile = "/data/$(id)/`x`;rm -rf"

    await probe.observe([ObservationKind.FILE_CHANGES], [hostile], None)

    (argv,) = session.execs
    assert argv[:2] == ("sh", "-c") and hostile in argv[6:] and hostile not in argv[2]


async def test_no_session_means_nothing_observed_and_a_reason() -> None:
    probe = SessionWatchProbe(DEVICE_ID, FakeClock())

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert (observation.processes, observation.resources) == ((), None)
    assert observation.log_lines == () and observation.file_changes == ()
    assert "no open session" in observation.reason


async def test_a_closed_session_is_treated_as_none() -> None:
    probe, session, _ = _rig(_busy_machine())
    await session.close()

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert observation.processes == () and "no open session" in observation.reason


async def test_the_session_can_be_taken_back() -> None:
    probe, session, _ = _rig(_busy_machine())
    probe.use_session(None)

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert observation.processes == () and session.execs == []


async def test_a_platform_without_collectors_runs_nothing() -> None:
    probe, session, _ = _rig(_busy_machine(), platform="darwin")

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert observation.processes == () and observation.resources is None
    assert "darwin" in observation.reason
    assert session.execs == []


# ──────────────────────────────────────────────────────────────────────────────
# What the machine can do to it
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_file_name_that_looks_like_a_marker_cannot_forge_or_end_a_section() -> None:
    machine = _busy_machine()
    forged = (
        f"{_ROOT}/x\n@@feedc0de00112233 stat\ncpu0 9\nbtime 1\n@@ procs\n@@feedc0de00112233 r1\nz"
    )
    machine.files[_ROOT].append((forged, _NOW - 1.0, 1))
    probe, _session, _ = _rig(machine)

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert [p.name for p in observation.processes] == ["worker", "init", "idle"]
    assert forged in {change.path for change in observation.file_changes}
    assert observation.resources is not None and observation.resources.uptime_s == 1_000.0


async def test_a_log_that_prints_a_marker_line_cannot_relabel_its_lines() -> None:
    machine = _busy_machine()
    machine.logs[f"{_ROOT}/app.log"] = "real\n@@feedc0de00112233 tail2\nfake\n"
    probe, _session, _ = _rig(machine)

    observation = await probe.observe([ObservationKind.LOGS], [_ROOT], None)

    assert {line.path for line in observation.log_lines} == {f"{_ROOT}/app.log"}
    assert [line.line for line in observation.log_lines] == [
        "real",
        "@@feedc0de00112233 tail2",
        "fake",
    ]


async def test_a_tail_that_times_out_costs_only_the_logs() -> None:
    machine = _busy_machine()

    def respond(spec: ExecSpec) -> CompletedCommand:
        if not spec.argv[2].startswith("mark=$1; dir=$2"):
            raise CommandTimeoutError(spec.argv, spec.timeout_s)
        return machine.respond(spec)

    clock = FakeClock()
    probe = SessionWatchProbe(DEVICE_ID, clock, _LINUX)
    probe.use_session(RecordingSession(Path("/scratch"), clock, machine, respond))

    observation = await probe.observe(_ALL, [_ROOT], None)

    assert observation.processes != () and observation.resources is not None
    assert observation.log_lines == ()
    assert observation.reason == "Not collected: LOGS."


async def test_the_first_command_timing_out_is_an_error_the_observer_catches() -> None:
    machine = _busy_machine()
    clock = FakeClock()

    def hang(spec: ExecSpec) -> CompletedCommand:
        raise CommandTimeoutError(spec.argv, spec.timeout_s)

    probe = SessionWatchProbe(DEVICE_ID, clock, _LINUX)
    probe.use_session(RecordingSession(Path("/scratch"), clock, machine, hang))

    with pytest.raises(CommandTimeoutError) as caught:
        await probe.observe(_ALL, [_ROOT], None)

    assert isinstance(caught.value, PROBE_FAILURES)


async def test_a_session_that_closes_mid_observation_raises_session_closed() -> None:
    machine = _busy_machine()
    clock = FakeClock()

    def closed(spec: ExecSpec) -> CompletedCommand:
        raise SessionClosedError(Path("/scratch"))

    probe = SessionWatchProbe(DEVICE_ID, clock, _LINUX)
    probe.use_session(RecordingSession(Path("/scratch"), clock, machine, closed))

    with pytest.raises(SessionClosedError):
        await probe.observe(_ALL, [_ROOT], None)


@settings(max_examples=40, deadline=None)
@given(
    names=st.lists(
        st.text(st.characters(codec="utf-8", exclude_characters="\0"), max_size=40),
        max_size=12,
    ),
    ticks=st.lists(st.integers(min_value=0, max_value=10**9), min_size=12, max_size=12),
)
def test_whatever_the_machine_calls_its_processes_the_observation_is_valid(
    names: list[str], ticks: list[int]
) -> None:
    machine = ScriptedMachine(boot_epoch=_BOOT, uptime_s=500.0)
    machine.processes = [
        FakeProcess(pid, name, utime=ticks[pid - 1], started_s=pid)
        for pid, name in enumerate(names, 1)
    ]
    probe, _session, _ = _rig(machine)

    observation = asyncio.run(probe.observe([ObservationKind.PROCESS_LIST], [], None))

    assert all(0.0 <= p.cpu_share <= 1.0 and len(p.name) <= 256 for p in observation.processes)
    assert Observation.model_validate_json(observation.model_dump_json()) == observation
