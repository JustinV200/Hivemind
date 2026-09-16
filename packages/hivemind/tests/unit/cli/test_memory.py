"""Tests for hivemind.cli.memory: `hive memory show|pins|compact|wax`.

Fits into the Hive:
    Mirrors src/hivemind/cli/memory/ (codingrules section 3: tests/unit mirrors src one-to-one;
    the CLI test convention keeps this flat under tests/unit/cli, matching test_cells.py's own
    placement for a command whose source lives one package level deeper). Drives the typer
    application through `typer.testing.CliRunner`, against a real `builders.cli.fake_manifest`
    manifest and a real SQLite file, the same pattern `test_cells.py`/`test_inbox.py` use.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.memory for the package under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.stores import open_memory
from hivemind.memory import WaxState
from waggle.ids import CellId

runner = CliRunner()


def _db_path(manifest_path: Path) -> Path:
    """Return the SQLite file `builders.cli.fake_manifest` wrote `[hive] db` as."""
    return manifest_path.parent / "data" / "hive.sqlite3"


def test_pins_add_then_list_round_trips_through_sqlite(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    add_result = runner.invoke(
        app,
        [
            "memory",
            "pins",
            "add",
            "Always check disk space first.",
            "--manifest",
            str(manifest_path),
        ],
    )
    assert add_result.exit_code == 0, add_result.output
    pin_id = add_result.output.strip()

    list_result = runner.invoke(app, ["memory", "pins", "list", "--manifest", str(manifest_path)])

    assert list_result.exit_code == 0, list_result.output
    assert pin_id in list_result.output
    assert "Always check disk space first." in list_result.output


def test_pins_remove_is_idempotent(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(
        app, ["memory", "pins", "remove", "evt_unknown", "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 0, result.output
    assert "removed" in result.output


def test_wax_propose_leaves_a_proposed_row(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    cell_id = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    result = runner.invoke(
        app,
        [
            "memory",
            "wax",
            "--manifest",
            str(manifest_path),
            cell_id,
            "propose",
            "This device is flaky over WiFi.",
            "--severity",
            "CAUTION",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PROPOSED" in result.output

    store = open_memory(_db_path(manifest_path))
    notes = asyncio.run(
        store.list_wax(CellId(cell_id), frozenset({WaxState.PROPOSED}), HoneyClearance.C2)
    )
    assert len(notes) == 1
    assert notes[0].text == "This device is flaky over WiFi."
    assert notes[0].severity.value == "CAUTION"


def test_wax_list_shows_a_proposed_note(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    cell_id = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    runner.invoke(
        app,
        ["memory", "wax", "--manifest", str(manifest_path), cell_id, "propose", "Note text."],
    )

    result = runner.invoke(
        app, ["memory", "wax", "--manifest", str(manifest_path), cell_id, "list"]
    )

    assert result.exit_code == 0, result.output
    assert "PROPOSED" in result.output
    assert "Note text." in result.output


def test_wax_clear_refuses_a_proposed_note(tmp_path: Path) -> None:
    """`clear` only ever moves WRITTEN -> CLEARED; a still-PROPOSED note is refused, not crashed."""
    manifest_path = fake_manifest(tmp_path)
    cell_id = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    propose_result = runner.invoke(
        app,
        ["memory", "wax", "--manifest", str(manifest_path), cell_id, "propose", "Note text."],
    )
    wax_id = propose_result.output.split()[1]

    result = runner.invoke(
        app, ["memory", "wax", "--manifest", str(manifest_path), cell_id, "clear", wax_id]
    )

    assert result.exit_code != 0


def test_show_command_prints_the_principal_and_token_count(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    runner.invoke(
        app, ["memory", "pins", "add", "A pinned fact.", "--manifest", str(manifest_path)]
    )

    result = runner.invoke(app, ["memory", "show", "worker-1", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "principal: worker-1" in result.output
    assert "A pinned fact." in result.output


def test_compact_command_reports_nothing_to_compact_when_no_closed_tasks(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["memory", "compact", "warden-1", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "nothing to compact" in result.output


def test_show_command_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["memory", "show", "worker-1", "--manifest", str(tmp_path / "missing.toml")]
    )

    assert result.exit_code == 2
