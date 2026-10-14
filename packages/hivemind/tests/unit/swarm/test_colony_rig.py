"""Unit: the parts of the Nuc scenario's rig that need no docker daemon.

The phase 11 Nuc scenario (`tests/e2e/test_phase11_exit_nuc.py`) proves the rig whole against a
real container. What can go wrong before the container exists is checked here in milliseconds, on
every run: that the tables the Hive's manifest gains and the device's own manifest load through
the schemas that read them and agree on the server and the models (the Hive allows two of the
three the device lists), what the scripted servers' rounds are, how a container is created, filled
and started when it has files to be given first, and the command that installs the packet between
enrolling and running.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises `e2e.swarm.colony`,
    `e2e.swarm.container`'s files and `e2e.swarm.enrolled`'s command; nothing here starts a
    container or touches a network.

Key invariants:
    - Docker is replaced by a recorder, so no test here depends on a daemon being present.
    - No test sleeps.

See Also:
    - tests/unit/swarm/test_device_rig.py and test_scenario_rig.py for the rest of the rig.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from builders.forage import make_capacity
from builders.swarm.manifest import swarm_manifest
from e2e.swarm import container as container_module
from e2e.swarm.colony import (
    ALLOWED,
    ROGUE,
    SERVER_ID,
    SMALL,
    colonizable,
    device_manifest,
    hive_tables,
    local_scenario,
    scenario_of,
    shared_scenario,
)
from e2e.swarm.colony.device import _EVENTS_READER, _IDS_READER, _OUTBOX_READER, _utc
from e2e.swarm.colony.models import LOCAL_SCENARIO, SCRIPTED_SERVER, SEATS, WARDEN_MANIFEST
from e2e.swarm.container import BRIDGE, CommandResult, ContainerSpec, Janitor, Network
from e2e.swarm.enrolled import ENROL_AND_RUN, _command

from hivemind.cli.in_cell.borrowed.llm import device_llm
from hivemind.cli.stores import server_configs
from hivemind.manifest import load_manifest
from hivemind.manifest.device import load_device_manifest
from hivemind.swarm import NucFacts, OfferedServer, RuntimeLevel, decide_nuc

URL = "http://172.20.0.1:40123/v1"
REPORT = "report.txt"


class Recorder:
    """Stands in for `container.docker`: records every call and succeeds."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        """Record the call and answer with an empty success."""
        self.calls.append(args)
        return CommandResult(("docker", *args), 0, "", "")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Replace the module's docker call with a recorder, for the janitor under test."""
    fake = Recorder()
    monkeypatch.setattr(container_module, "docker", fake)
    return fake


def test_the_tables_the_hive_gains_list_a_server_it_never_starts_and_two_models(
    tmp_path: Path,
) -> None:
    path = swarm_manifest(tmp_path)
    path.write_text(f"{path.read_text('utf-8')}\n{hive_tables()}\n", encoding="utf-8")

    manifest = load_manifest(path, {})

    server = manifest.llm.servers[SERVER_ID]
    assert server.load_on_start is False  # The Hive Stand starts no copy: the device's Warden does.
    assert tuple(server.sources) == ALLOWED
    assert ROGUE not in manifest.forage.map  # The Hive has never heard of the third model.


def test_the_device_lists_the_same_server_with_one_model_more_than_the_hive_allows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "warden.toml"
    path.write_text(device_manifest(URL), encoding="utf-8")

    manifest = load_device_manifest(path)

    server = manifest.llm.servers[SERVER_ID]
    assert tuple(server.sources) == (*ALLOWED, ROGUE)  # The allowlist is the Hive's, not this.
    assert set(manifest.forage.map) == {*ALLOWED, ROGUE}
    assert manifest.llm.providers["shared"].base_url == URL


def test_the_device_manifest_converts_to_the_tables_its_warden_is_built_with(
    tmp_path: Path,
) -> None:
    path = tmp_path / "warden.toml"
    path.write_text(device_manifest(URL), encoding="utf-8")
    manifest = load_device_manifest(path)

    converted = device_llm(manifest.llm, manifest.forage.map)

    assert [server.server_id for server in converted.servers] == [SERVER_ID]
    assert converted.hostable[SERVER_ID].load_on_start is False  # Started only when she asks.
    rows = {row.key: row for row in converted.slots}
    assert rows["worker"].fallback == "local_worker"  # A Nuc's bees go to its own model first.
    assert rows["local_worker"].provider == "nuc_local"
    # A Capping review of a Drone's command asks the judge: with the link cut it must have a local
    # one, or a sampled review would fail a bee that was getting on fine with its own model.
    assert rows["judge"].fallback == "local_judge" and rows["local_judge"].provider == "nuc_local"
    assert rows["queen"].fallback is None  # Every other slot is the Hive's, and waits for it.


def test_the_queen_would_promote_a_container_with_no_gpu_to_a_nuc_on_what_the_hive_allows(
    tmp_path: Path,
) -> None:
    path = swarm_manifest(tmp_path)
    path.write_text(f"{path.read_text('utf-8')}\n{hive_tables()}\n", encoding="utf-8")
    manifest = load_manifest(path, {})
    offered = tuple(
        OfferedServer(server_id, config.sources)
        for server_id, config in server_configs(manifest).items()
    )
    facts = NucFacts(
        level=RuntimeLevel.COLONIZED,
        capacity=make_capacity(),  # A modest host with no GPU, as a container reports itself.
        declared=(SERVER_ID,),
        offered=offered,
        desired_level=RuntimeLevel.NUC,
        hive_pressure=0.0,
        seat_pressure=0.75,
    )

    decision = decide_nuc(facts)

    # Every model the Hive allows is a CPU model (it declares no VRAM, so any device fits it): a
    # first run of the Nuc scenario declared one and the Queen refused with `vram_too_small`.
    assert decision.should_promote and decision.server_id == SERVER_ID
    assert decision.loadable == decision.initial == ALLOWED
    assert ROGUE not in decision.loadable
    assert manifest.forage.map[SMALL].grade == 2 and manifest.forage.map[SMALL].seats == SEATS


def test_both_scripted_servers_make_the_same_rounds_and_the_local_one_ends_with_a_cause() -> None:
    shared = shared_scenario(3, 2, REPORT)
    local = local_scenario(3, 2, REPORT)

    assert shared["steps"] == local["steps"]
    rounds = shared["steps"]
    assert isinstance(rounds, list) and len(rounds) == 4  # Three commands, then the report.
    assert rounds[0] == {"tool": "run_command", "arguments": {"argv": ["sleep", "2"]}}
    assert rounds[-1]["tool"] == "write_file" and rounds[-1]["arguments"]["path"] == REPORT
    assert str(local["closing"]).startswith("CAUSE:")  # How a diagnosing Drone reports.
    assert not str(shared["closing"]).startswith("CAUSE:")


def test_the_in_process_scenario_has_the_same_rounds_as_the_file_the_other_server_reads() -> None:
    fields = shared_scenario(2, 1, REPORT)

    scenario = scenario_of(fields)

    assert scenario.steps is not None
    assert [(step.tool, dict(step.arguments)) for step in scenario.steps] == [
        ("run_command", {"argv": ["sleep", "1"]}),
        ("run_command", {"argv": ["sleep", "1"]}),
        ("write_file", {"path": REPORT, "content": "done"}),
    ]
    assert scenario.closing == fields["closing"]


def test_a_colonizable_devices_options_copy_three_files_in_and_install_the_packet(
    tmp_path: Path,
) -> None:
    network = Network("test-net")

    options = colonizable(tmp_path, network, URL, local_scenario(2, 1, REPORT))

    assert set(options.files) == {WARDEN_MANIFEST, LOCAL_SCENARIO, SCRIPTED_SERVER}
    assert all(source.is_file() for source in options.files.values())
    assert json.loads(options.files[LOCAL_SCENARIO].read_text("utf-8"))["steps"]
    assert options.install[:2] == ("--runtime-wheelhouse", "/opt/wheelhouse")
    assert options.install[2:] == ("--warden-manifest", WARDEN_MANIFEST)
    assert options.network is network


def test_a_devices_manifest_carries_the_supervision_a_scenario_gives_it(tmp_path: Path) -> None:
    table = "[supervision]\nmax_offline_s = 90\n[supervision.reconnect_budget]\nwall_clock_s = 40\n"
    options = colonizable(tmp_path, Network("test-net"), URL, local_scenario(2, 1, REPORT), table)

    manifest = load_device_manifest(options.files[WARDEN_MANIFEST])

    # What lets a scenario cut a device off and see the outcome in seconds, not in ten minutes.
    assert manifest.supervision.max_offline_s == 90.0
    assert manifest.supervision.reconnect_budget.wall_clock_s == 40.0


def test_a_manifest_with_no_supervision_given_has_none_and_so_the_shipped_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "warden.toml"
    path.write_text(device_manifest(URL), encoding="utf-8")

    assert "[supervision]" not in path.read_text("utf-8")
    assert load_device_manifest(path).supervision.max_offline_s == 600.0


def test_the_command_of_a_plain_device_is_the_one_every_scenario_has_always_run() -> None:
    assert _command(()) == ENROL_AND_RUN


def test_the_command_of_a_colonizable_device_installs_between_enrolling_and_running() -> None:
    command = _command(
        ("--runtime-wheelhouse", "/opt/wheelhouse", "--warden-manifest", "/opt/w.toml")
    )

    enrol, install, run = command.split(" && ")
    assert enrol.startswith("pollen enrol") and run.startswith("exec pollen run")
    assert install == (
        'pollen install --data-dir "$POLLEN_DATA_DIR" '
        "--runtime-wheelhouse /opt/wheelhouse --warden-manifest /opt/w.toml"
    )


def test_a_container_with_files_is_created_filled_and_then_started(
    recorder: Recorder, tmp_path: Path
) -> None:
    source = tmp_path / "warden.toml"
    source.write_text("[llm]\n", encoding="utf-8")
    spec = ContainerSpec(image="device", command=("true",), files={"/opt/warden.toml": source})

    started = Janitor().start(spec)

    verbs = [call[0] for call in recorder.calls]
    assert verbs == ["create", "cp", "start"]  # Filled before the command that reads it can run.
    assert recorder.calls[1] == ("cp", str(source), f"{started.name}:/opt/warden.toml")
    assert "--detach" not in recorder.calls[0] and "--init" in recorder.calls[0]


def test_a_container_without_files_is_started_in_one_run_as_it_always_was(
    recorder: Recorder,
) -> None:
    Janitor().start(ContainerSpec(image="device", command=("true",), network=BRIDGE.name))

    [call] = recorder.calls
    assert call[:3] == ("run", "--detach", "--init")


def _run_reader(program: str, *arguments: str, capsys: pytest.CaptureFixture[str]) -> object:
    """Run a program the rig sends to the device, as the device's interpreter would, here."""
    previous = sys.argv
    sys.argv = ["-c", *arguments]
    try:
        exec(compile(program, "<on-device reader>", "exec"), {})  # noqa: S102 - the rig's own text
    finally:
        sys.argv = previous
    return json.loads(capsys.readouterr().out)


def test_the_outbox_reader_lists_what_was_queued_and_not_acknowledged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frames = {
        "msg_1": {"kind": "task.result", "payload": {"task_id": "task_a"}},
        "msg_2": {"kind": "forage.request", "payload": {"task_id": "task_b"}},
        "msg_3": {"kind": "honey.nectar_deposit", "payload": {}},
    }
    lines = [{"op": "put", "id": key, "frame": json.dumps(frame)} for key, frame in frames.items()]
    lines.append({"op": "ack", "id": "msg_1"})  # The Queen had this one before the cut.
    path = tmp_path / "outbox.jsonl"
    path.write_text("".join(f"{json.dumps(line)}\n" for line in lines), encoding="utf-8")

    queued = _run_reader(_OUTBOX_READER, str(path), capsys=capsys)

    assert queued == [["forage.request", "task_b"], ["honey.nectar_deposit", None]]


def test_the_trail_reader_gives_the_bodies_of_one_kind_oldest_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "warden.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table pheromone_events (at text, kind text, body text)")
        rows = [
            ("2026-01-01T00:00:02Z", "warden.offline", json.dumps({"n": 2})),
            ("2026-01-01T00:00:01Z", "warden.offline", json.dumps({"n": 1})),
            ("2026-01-01T00:00:03Z", "worker.done", json.dumps({"n": 3})),
        ]
        db.executemany("insert into pheromone_events values (?, ?, ?)", rows)
    db.close()

    found = _run_reader(_EVENTS_READER, str(path), "warden.offline", capsys=capsys)

    assert found == [{"n": 1}, {"n": 2}]


def test_the_id_reader_gives_every_event_with_the_time_it_was_recorded_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "warden.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table pheromone_events (id text, at text, kind text, body text)")
        rows = [
            ("event_a", "2026-01-01T00:00:01.250000+00:00", "worker.done", "{}"),
            ("event_b", "2026-01-01T00:00:02+00:00", "warden.offline", "{}"),
        ]
        db.executemany("insert into pheromone_events values (?, ?, ?, ?)", rows)
    db.close()

    found = _run_reader(_IDS_READER, str(path), capsys=capsys)

    assert found == [
        ["event_a", "2026-01-01T00:00:01.250000+00:00"],
        ["event_b", "2026-01-01T00:00:02+00:00"],
    ]


def test_a_time_the_device_wrote_is_read_as_the_same_instant_however_it_ends() -> None:
    assert _utc("2026-01-01T00:00:01.5Z") == _utc("2026-01-01T00:00:01.500000+00:00")
    assert _utc("2026-01-01T02:00:00+02:00") == _utc("2026-01-01T00:00:00Z")
    assert _utc("2026-01-01T00:00:00Z").utcoffset() == timedelta(0)
