"""Tests for hivemind.cli.tasks: `hive tasks submit|list|show`.

Fits into the Hive:
    Mirrors src/hivemind/cli/tasks.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Drives the typer application through typer.testing.CliRunner, the same way an
    operator's shell would, rather than calling the command functions directly.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.tasks for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app
from waggle.clock import SystemClock
from waggle.ids import new_hive_id, new_node_id, new_task_id

runner = CliRunner()

# `hive tasks submit` is the one store command with no `--db` (codingrules 5.1's parameter
# cap), so every test that submits names a Hive instead. fake_manifest writes hive.toml
# with `[hive] db` pointing here, resolved against the manifest's own directory.
_MANIFEST_DB = Path("data") / "hive.sqlite3"

# A minimal two-task graph: `build` depends on `plan`. Reused by every test that needs a valid
# TaskGraphDraft file on disk.
_GRAPH = {
    "tasks": [
        {
            "key": "plan",
            "title": "Plan",
            "objective": "Plan the work.",
            "acceptance": [
                {"kind": "FILE_EXISTS", "subject": "scratch/done.txt", "argv": [], "expected": None}
            ],
        },
        {
            "key": "build",
            "title": "Build",
            "objective": "Build the thing.",
            "acceptance": [
                {"kind": "FILE_EXISTS", "subject": "scratch/done.txt", "argv": [], "expected": None}
            ],
            "depends_on": ["plan"],
        },
    ]
}


def _write_graph(path: Path) -> None:
    path.write_text(json.dumps(_GRAPH), encoding="utf-8")


def _fresh_ids() -> tuple[str, str]:
    clock = SystemClock()
    return str(new_hive_id(clock)), str(new_node_id(clock))


def test_submit_prints_one_line_per_task_in_graph_order(tmp_path: Path) -> None:
    graph_file = tmp_path / "graph.json"
    _write_graph(graph_file)
    hive_id, node_id = _fresh_ids()

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
            "--manifest",
            str(fake_manifest(tmp_path)),
        ],
    )

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert [line.split("\t")[0] for line in lines] == ["plan", "build"]
    assert all(line.split("\t")[2] == "PENDING" for line in lines)


def test_submit_bad_json_exits_2_with_pydantics_message(tmp_path: Path) -> None:
    graph_file = tmp_path / "graph.json"
    graph_file.write_text("not json", encoding="utf-8")
    hive_id, node_id = _fresh_ids()

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
            "--manifest",
            str(fake_manifest(tmp_path)),
        ],
    )

    assert result.exit_code == 2
    assert "validation error" in result.output.lower()


def test_submit_bad_hive_id_exits_2() -> None:
    _, node_id = _fresh_ids()

    result = runner.invoke(
        app,
        ["tasks", "submit", "unused.json", "--hive-id", "not-an-id", "--node-id", node_id],
    )

    assert result.exit_code == 2


def test_submit_bad_node_id_exits_2() -> None:
    hive_id, _ = _fresh_ids()

    result = runner.invoke(
        app,
        ["tasks", "submit", "unused.json", "--hive-id", hive_id, "--node-id", "not-an-id"],
    )

    assert result.exit_code == 2


def test_list_and_show_round_trip_through_the_same_database(tmp_path: Path) -> None:
    graph_file = tmp_path / "graph.json"
    _write_graph(graph_file)
    hive_id, node_id = _fresh_ids()
    manifest = fake_manifest(tmp_path)
    db = tmp_path / _MANIFEST_DB
    submitted = runner.invoke(
        app,
        [
            "tasks",
            "submit",
            str(graph_file),
            "--hive-id",
            hive_id,
            "--node-id",
            node_id,
            "--manifest",
            str(manifest),
        ],
    )
    task_id = submitted.stdout.splitlines()[0].split("\t")[1]

    listed = runner.invoke(app, ["tasks", "list", "--db", str(db)])
    assert listed.exit_code == 0
    assert task_id in listed.stdout

    shown = runner.invoke(app, ["tasks", "show", task_id, "--db", str(db)])
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["id"] == task_id


def test_list_filters_by_status(tmp_path: Path) -> None:
    graph_file = tmp_path / "graph.json"
    _write_graph(graph_file)
    hive_id, node_id = _fresh_ids()
    manifest = fake_manifest(tmp_path)
    db = tmp_path / _MANIFEST_DB
    runner.invoke(
        app,
        [
            "tasks",
            "submit",
            str(graph_file),
            "--hive-id",
            hive_id,
            "--node-id",
            node_id,
            "--manifest",
            str(manifest),
        ],
    )

    result = runner.invoke(app, ["tasks", "list", "--db", str(db), "--status", "RUNNING"])

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1  # header only: both submitted tasks are PENDING, not RUNNING
    assert lines[0].startswith("ID")


def test_show_unknown_task_exits_1(tmp_path: Path) -> None:
    unknown_id = str(new_task_id(SystemClock()))

    result = runner.invoke(
        app, ["tasks", "show", unknown_id, "--db", str(tmp_path / "hive.sqlite3")]
    )

    assert result.exit_code == 1
    assert "No task with id" in result.output
