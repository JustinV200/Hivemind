"""Tests for hivemind.cli.readback.leavings: `hive cells leavings list|remove`.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/leavings.py (codingrules section 3). Drives the typer
    application through `typer.testing.CliRunner` against a real `builders.cli.fake_manifest`
    manifest, seeding its `[hive] db` file directly through `hivemind.cli.stores.open_leavings`
    the same way a real `hivemind.cell.local.HiveStandLeaseReleaser.release` would have left it
    (this command's own job is reading/clearing state a *different* process wrote).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.leavings for the module under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from builders.cells import make_leaving
from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cell.leavings import Leaving
from hivemind.cli.app import app
from hivemind.cli.stores import open_leavings
from hivemind.manifest import load_manifest
from hivemind.pheromone import CellEvent
from waggle.clock import FakeClock
from waggle.ids import CellId, new_event_id, new_hive_id, new_node_id

runner = CliRunner()


def _seed_leaving(
    manifest_path: Path,
    tmp_path: Path,
    *,
    prior: bytes | None,
    relative_path: str = "left/report.txt",
) -> Leaving:
    """Open the manifest's own db and record one Leaving at a real file under `tmp_path`.

    A fresh `FakeClock` per call (`make_leaving`'s own `cell_id` default) means two calls always
    land on two different Cells, which is what the "list/remove across every Cell" tests need.
    """
    manifest = load_manifest(manifest_path, {})
    db = manifest.resolve_path(manifest.hive.db)
    clock = FakeClock()
    left_path = tmp_path / relative_path
    left_path.parent.mkdir(parents=True, exist_ok=True)
    left_path.write_bytes(b"the report the task left behind")
    leaving = make_leaving(clock=clock, path=left_path.resolve(strict=False), prior=prior)
    event = CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="cell.left",
        subject_id=leaving.cell_id,
        payload={},
    )
    asyncio.run(open_leavings(db).record_leaving(leaving, event))
    return leaving


def test_list_command_prints_nothing_for_a_cell_with_no_leavings(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(
        app,
        ["cells", "leavings", "list", str(CellId("cell_test")), "--manifest", str(manifest_path)],
    )

    assert result.exit_code == 0, result.output


def test_list_command_json_reports_the_recorded_leaving(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    leaving = _seed_leaving(manifest_path, tmp_path, prior=None)

    result = runner.invoke(
        app,
        [
            "cells",
            "leavings",
            "list",
            str(leaving.cell_id),
            "--manifest",
            str(manifest_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["path"] == str(leaving.path)
    assert rows[0]["sha256"] == leaving.sha256
    assert rows[0]["approved_by"] == "policy"
    assert rows[0]["removed_at"] is None
    assert "prior" not in rows[0]  # codingrules section 12: never a raw content dump.


def test_remove_command_unlinks_when_prior_is_none_and_marks_the_row_removed(
    tmp_path: Path,
) -> None:
    manifest_path = fake_manifest(tmp_path)
    leaving = _seed_leaving(manifest_path, tmp_path, prior=None)
    assert leaving.path.exists()

    result = runner.invoke(
        app, ["cells", "leavings", "remove", str(leaving.cell_id), "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 0, result.output
    assert not leaving.path.exists()
    # A second run finds nothing active left to remove: idempotent by absence, not by error.
    second = runner.invoke(
        app, ["cells", "leavings", "remove", str(leaving.cell_id), "--manifest", str(manifest_path)]
    )
    assert second.exit_code == 0, second.output
    assert "removed 0 leaving" in second.output


def test_remove_command_replays_prior_bytes(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    leaving = _seed_leaving(manifest_path, tmp_path, prior=b"the bytes before the write")

    result = runner.invoke(
        app, ["cells", "leavings", "remove", str(leaving.cell_id), "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 0, result.output
    assert leaving.path.read_bytes() == b"the bytes before the write"


def test_list_command_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "cells",
            "leavings",
            "list",
            str(CellId("cell_test")),
            "--manifest",
            str(tmp_path / "missing.toml"),
        ],
    )

    assert result.exit_code == 2


# ──────────────────────────────────────────────────────────────────────────────
# CELL-optional list/remove (the "which Cell did `hive run` mint this time" fix)
# ──────────────────────────────────────────────────────────────────────────────


def test_list_command_with_no_cell_spans_every_cell(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    first = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/a.txt")
    second = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/b.txt")
    assert first.cell_id != second.cell_id  # Two different Cells, the scenario this covers.

    result = runner.invoke(app, ["cells", "leavings", "list", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert str(first.cell_id) in result.output
    assert str(second.cell_id) in result.output


def test_list_command_json_with_no_cell_includes_a_cell_column_per_row(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    first = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/a.txt")
    second = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/b.txt")

    result = runner.invoke(
        app, ["cells", "leavings", "list", "--manifest", str(manifest_path), "--json"]
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    cell_ids = {row["cell_id"] for row in rows}
    assert cell_ids == {str(first.cell_id), str(second.cell_id)}


def test_remove_command_with_neither_cell_nor_path_is_a_usage_error(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cells", "leavings", "remove", "--manifest", str(manifest_path)])

    assert result.exit_code == 2, result.output


def test_remove_command_with_path_only_removes_the_one_matching_row(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    target = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/target.txt")
    other = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/other.txt")

    result = runner.invoke(
        app,
        [
            "cells",
            "leavings",
            "remove",
            "--path",
            str(target.path),
            "--manifest",
            str(manifest_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not target.path.exists()
    assert other.path.exists()  # A different path on a different Cell: untouched.


def test_remove_command_with_path_only_refuses_when_active_on_more_than_one_cell(
    tmp_path: Path,
) -> None:
    manifest_path = fake_manifest(tmp_path)
    # Two different Cells (two _seed_leaving calls) both leaving the exact same resolved path.
    first = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/shared.txt")
    second = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/shared.txt")
    assert first.path == second.path
    assert first.cell_id != second.cell_id

    result = runner.invoke(
        app,
        [
            "cells",
            "leavings",
            "remove",
            "--path",
            str(first.path),
            "--manifest",
            str(manifest_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "more than one Cell" in result.output
    assert first.path.exists()  # Refused: neither row was touched.


def test_remove_command_with_cell_and_path_scopes_to_that_cell(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    first = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/shared.txt")
    second = _seed_leaving(manifest_path, tmp_path, prior=None, relative_path="left/shared.txt")

    result = runner.invoke(
        app,
        [
            "cells",
            "leavings",
            "remove",
            str(first.cell_id),
            "--path",
            str(first.path),
            "--manifest",
            str(manifest_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not first.path.exists()
    # `second`'s row lives on a different Cell than `first`'s, keyed independently in the store,
    # so removing `first`'s row must leave it untouched even though both share one real path.
    manifest = load_manifest(manifest_path, {})
    db = manifest.resolve_path(manifest.hive.db)
    remaining = asyncio.run(open_leavings(db).get_leaving(second.cell_id, second.path))
    assert remaining.removed_at is None


def test_remove_command_with_path_only_reports_no_active_leaving(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(
        app,
        [
            "cells",
            "leavings",
            "remove",
            "--path",
            str(tmp_path / "never-left.txt"),
            "--manifest",
            str(manifest_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "no active Leaving" in result.output
