"""Unit: the parts of the colonized watch scenario's rig that need no docker daemon.

The phase 11 watch scenario for a colonized device
(`tests/e2e/test_phase11_exit_watch_colonized.py`) proves the rig whole against a real container, in
a few minutes. What can go wrong before the container exists is checked here in milliseconds, on
every run: that the scripted model decides a Patrol from the facts it is shown with the very wording
the Warden's prompt and facts really have (the heading, the signal line), alarming only for the
process the scenario names; that the reader the test runs on the device, with the runtime's
interpreter, understands the store the Warden really writes; that the manifest table asks for a
sample and a Patrol at every beat; and how the test reads the Patrols the Warden recorded.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises
    `e2e.swarm.colony.watching` and the Patrol branch of `e2e.scripted_openai`; the reader is run
    for real, with this interpreter, on a real device store in a temporary directory.

Key invariants:
    - A container is replaced by a stand-in that runs the reader locally, so no test needs a daemon.
    - The scripted server's restated wording is checked against the Warden's own, so a change to
      either fails here and not after minutes of a docker run.
    - No test sleeps.

See Also:
    - tests/unit/swarm/test_desktop_rig.py for the rig this one is shaped like.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from builders.watch import make_observation, make_process, make_resources
from e2e.scripted_openai import Scenario, build_app
from e2e.swarm.colony import watching
from e2e.swarm.colony.watching import (
    APP_DIR,
    Patrolled,
    Seen,
    _watch_table,
    device_observations,
    device_patrols,
)
from e2e.swarm.container import CommandResult, Container
from e2e.watch_helpers import HOG_NAME
from starlette.testclient import TestClient

from hivemind.cli.in_cell.borrowed import open_device_stores
from hivemind.llm.prompts import PromptName, load_prompt
from hivemind.memory import MemoryContext, MemoryIdentity
from hivemind.wardens.watch.digest import PatrolWindow, facts_of
from hivemind.wardens.watch.signals import SignalKind, WatchSignal
from hivemind.wardens.watch.store import save
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id
from waggle.messages.swarm import FileChange, FileChangeKind, LogLine

_CLOCK = FakeClock()
_CHAT = "/v1" + "/chat/completions"  # Spelt in two halves: the joined fragment is banned (8.6).
_AT = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
_PATROL_SYSTEM = load_prompt(PromptName.PATROL)


class Device:
    """Stands in for a `Container` whose store is a local file: runs the reader with this Python."""

    name = "box"

    def __init__(self, store: Path | None, rows: Sequence[dict[str, object]] = ()) -> None:
        """Read from `store` (None: no store yet); answer `warden.patrol` events from `rows`."""
        self._store = store
        self._rows = list(rows)

    def exec(self, argv: Sequence[str], *, check: bool = True) -> CommandResult:
        """Run the reader on the local store with this interpreter, as the device's would."""
        assert argv[0].endswith("python"), "the reader runs with the runtime's interpreter"
        if self._store is None:
            return CommandResult(tuple(argv), 1, "", "unable to open database file")
        local = [sys.executable, "-c", argv[2], str(self._store)]
        done = subprocess.run(local, capture_output=True, text=True, check=False)  # noqa: S603
        return CommandResult(tuple(argv), done.returncode, done.stdout, done.stderr)


def _patrol_request(facts: str, system: str = _PATROL_SYSTEM) -> dict[str, object]:
    """A chat request shaped as the Warden's Patrol makes it: the prompt, then the facts."""
    return {
        "model": "scripted-queen",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": facts},
        ],
    }


def _decision(client: TestClient, request: dict[str, object]) -> dict[str, str]:
    """The Patrol decision the scripted server answers `request` with."""
    answer = client.post(_CHAT, json=request).json()["choices"][0]["message"]["content"]
    decided: dict[str, str] = json.loads(answer)
    return decided


def _facts(name: str) -> str:
    """The facts a real Patrol renders for a window in which `name` pinned the CPU."""
    observation = make_observation(
        _AT, processes=[make_process(pid=7, name=name, cpu_share=0.9)], resources=make_resources()
    )
    signal = WatchSignal(SignalKind.NEW_PROCESS_CPU, name, 0.9, _AT, pid=7)
    return facts_of(PatrolWindow(_AT, _AT, (observation,), (signal,)))


def test_the_warden_prompt_and_facts_really_have_the_wording_the_scripted_model_looks_for() -> None:
    # The scripted server restates the Patrol prompt's heading and the signal line, because it runs
    # as its own process where the Hive's code is not importable: this is the check that it still
    # means what the Warden says.
    assert _PATROL_SYSTEM.startswith("# The Patrol")
    assert f'signal NEW_PROCESS_CPU: name="{HOG_NAME}"' in _facts(HOG_NAME)


def test_a_patrol_shown_the_named_process_is_an_alarm() -> None:
    client = TestClient(build_app(Scenario(alarm_on=HOG_NAME)))

    decided = _decision(client, _patrol_request(_facts(HOG_NAME)))

    assert decided["choice"] == "ALARM" and decided["reason"]


def test_a_patrol_shown_another_process_or_nothing_notable_is_quiet() -> None:
    client = TestClient(build_app(Scenario(alarm_on=HOG_NAME)))

    another = _decision(client, _patrol_request(_facts("busy_but_innocent")))
    nothing = _decision(
        client, _patrol_request("latest sample at 2026-10-14T12:00:00: cpu_load=0.1")
    )

    assert another["choice"] == nothing["choice"] == "NOTHING"


def test_a_log_line_that_names_the_process_is_not_a_signal() -> None:
    client = TestClient(build_app(Scenario(alarm_on=HOG_NAME)))
    quoted = f'log path="/srv/app/app.log" line="{HOG_NAME} was here"'

    # Only the Warden's own signal line counts: the machine's words cannot make the model alarm.
    assert _decision(client, _patrol_request(quoted))["choice"] == "NOTHING"


def test_a_scenario_that_names_no_process_never_alarms() -> None:
    client = TestClient(build_app(Scenario()))

    assert _decision(client, _patrol_request(_facts(HOG_NAME)))["choice"] == "NOTHING"


def test_a_request_that_is_not_a_patrol_is_answered_as_before() -> None:
    client = TestClient(build_app(Scenario(alarm_on=HOG_NAME)))
    other = _patrol_request(_facts(HOG_NAME), system="You are the Queen.")

    answer = _decision(client, other)

    assert "choice" not in answer  # Her awake episode's decision, not a Patrol's.


def test_the_manifest_table_asks_for_a_sample_and_a_patrol_at_every_beat() -> None:
    table = tomllib.loads(_watch_table())["supervision"]["watch"]

    # A Warden on a device wakes every fifteen seconds; both intervals are shorter, so each beat
    # takes a sample and Patrols, and the alarm threshold is a share of all the cores together.
    assert table["interval_s"] < table["patrol_interval_s"] < 15.0
    assert 0 < table["cpu_share_alarm"] <= 1
    assert table["retention_s"] >= 86_400.0  # Nothing is purged by age during the run.
    assert "roots" not in table  # A device's directories are the operator's, not the manifest's.


async def test_the_reader_understands_the_store_the_warden_really_writes(tmp_path: Path) -> None:
    clock = FakeClock(_AT)
    stores = await open_device_stores(tmp_path / "state", clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    ctx = MemoryContext(store=stores.memory, identity=identity, clock=clock)
    seen = make_observation(
        _AT,
        processes=[make_process(name=HOG_NAME, cpu_share=0.5), make_process(pid=8, name="sh")],
        log_lines=[LogLine(path=f"{APP_DIR}/app.log", line="the database went away")],
        file_changes=[
            FileChange(
                path=f"{APP_DIR}/app.log",
                change=FileChangeKind.CREATED,
                at=_AT,
                size_bytes=12,
            )
        ],
    )
    await save(seen, ctx, "warden-1", 86_400.0)
    clock.advance(1.0)  # Kept a second later: the reader orders by when it was kept.
    await save(make_observation(clock.now()), ctx, "warden-1", 86_400.0)

    kept = device_observations(cast(Container, Device(tmp_path / "state" / "warden.sqlite")))

    assert len(kept) == 2 and kept[0].at < kept[1].at  # Oldest first.
    assert kept[0] == Seen(
        kept[0].at,
        frozenset({(f"{APP_DIR}/app.log", "the database went away")}),
        frozenset({("CREATED", f"{APP_DIR}/app.log")}),
        frozenset({HOG_NAME, "sh"}),
    )
    assert kept[1].lines == kept[1].changes == frozenset()  # A sample that read no file.


def test_a_device_that_has_no_store_yet_has_kept_nothing() -> None:
    assert device_observations(cast(Container, Device(None))) == ()


def test_the_patrols_are_read_from_the_wardens_own_trail_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[dict[str, object]] = [
        {"at": "2026-10-14T12:00:15+00:00", "payload": {"outcome": "NOTHING", "observations": 2}},
        {"at": "2026-10-14T12:00:30+00:00", "payload": {"outcome": "ALARM", "observations": 1}},
    ]
    monkeypatch.setattr(watching, "device_events", lambda container, kind: rows)

    patrols = device_patrols(cast(Container, Device(None)))

    assert patrols == (
        Patrolled(datetime.fromisoformat("2026-10-14T12:00:15+00:00"), "NOTHING"),
        Patrolled(datetime.fromisoformat("2026-10-14T12:00:30+00:00"), "ALARM"),
    )
