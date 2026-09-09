"""Tests for hivemind.cli.trail: `hive trail tail|export|merge`.

Fits into the Hive:
    Mirrors src/hivemind/cli/trail.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Drives the typer application through typer.testing.CliRunner. `--follow` is
    driven by monkeypatching `hivemind.cli.trail.open_trail` and `hivemind.cli.trail.follow`
    directly (the small seam this module already exposes as module-level names), rather than
    depending on real signal delivery or wall-clock timing.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.trail for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import hivemind.cli.trail as trail_module
from hivemind.cli.app import app
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, TaskEvent
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_task_id

runner = CliRunner()

_GRAPH = {
    "tasks": [
        {
            "key": "plan",
            "title": "Plan",
            "objective": "Plan the work.",
            "acceptance": [
                {"kind": "FILE_EXISTS", "subject": "scratch/done.txt", "argv": [], "expected": None}
            ],
        }
    ]
}


def _submit_sample(tmp_path: Path, db: Path) -> str:
    """Submit one task through `hive tasks submit` and return the node id it used."""
    graph_file = tmp_path / "graph.json"
    graph_file.write_text(json.dumps(_GRAPH), encoding="utf-8")
    clock = FakeClock()
    hive_id, node_id = str(new_hive_id(clock)), str(new_node_id(clock))
    result = runner.invoke(
        app,
        [
            "tasks",
            "submit",
            str(graph_file),
            "--hive-id",
            hive_id,
            "--node-id",
            node_id,
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0
    return node_id


def test_tail_prints_one_line_per_event_oldest_first(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    _submit_sample(tmp_path, db)

    result = runner.invoke(app, ["trail", "tail", "--db", str(db)])

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    fields = lines[0].split("  ")
    assert len(fields) == 5  # at, kind, subject_id, actor, payload
    assert fields[1] == "task.submitted"


def test_export_then_merge_round_trips_into_a_second_database(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    node_id = _submit_sample(tmp_path, db)
    segment_file = tmp_path / "segment.json"

    exported = runner.invoke(app, ["trail", "export", node_id, str(segment_file), "--db", str(db)])
    assert exported.exit_code == 0
    assert "exported 1 events" in exported.stdout

    db2 = tmp_path / "hive2.sqlite3"
    merged = runner.invoke(app, ["trail", "merge", str(segment_file), "--db", str(db2)])
    assert merged.exit_code == 0
    assert "inserted 1 events" in merged.stdout

    merged_again = runner.invoke(app, ["trail", "merge", str(segment_file), "--db", str(db2)])
    assert "inserted 0 events" in merged_again.stdout


def test_merge_bad_segment_file_exits_2(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json", encoding="utf-8")

    result = runner.invoke(
        app, ["trail", "merge", str(bad_file), "--db", str(tmp_path / "hive.sqlite3")]
    )

    assert result.exit_code == 2


def _sample_event(clock: FakeClock) -> TaskEvent:
    """Build one well-formed TaskEvent for the --follow monkeypatch below."""
    return TaskEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="task.submitted",
        subject_id=new_task_id(clock),
        payload={},
    )


async def _one_event_then_interrupt(
    trail: object, clock: object, poll_interval_s: float
) -> AsyncIterator[PheromoneEvent]:
    """Stand in for hivemind.pheromone.follow: yield one event, then simulate Ctrl-C.

    Raising KeyboardInterrupt here reaches tail_command's `except` exactly as a real signal
    delivered mid-poll would, without depending on OS signal delivery or wall-clock timing.
    """
    yield _sample_event(FakeClock())
    raise KeyboardInterrupt


def test_tail_follow_prints_events_then_exits_cleanly_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_trail = MemoryPheromoneTrail(FakeClock())
    # Seam: hand the CLI a memory-backed trail and a follow() that raises KeyboardInterrupt after
    # one event (see _one_event_then_interrupt's docstring for why that stands in for Ctrl-C).
    monkeypatch.setattr(trail_module, "open_trail", lambda db: memory_trail)
    monkeypatch.setattr(trail_module, "follow", _one_event_then_interrupt)

    result = runner.invoke(
        app, ["trail", "tail", "--db", str(tmp_path / "hive.sqlite3"), "--follow"]
    )

    assert result.exit_code == 0
    assert "task.submitted" in result.stdout
