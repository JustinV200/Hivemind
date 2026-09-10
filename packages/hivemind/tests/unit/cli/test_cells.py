"""Tests for hivemind.cli.readback.cells: `hive cells list`.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/cells.py (codingrules section 3). Drives the typer application
    through `typer.testing.CliRunner`, against a real `builders.cli.fake_manifest` manifest (this
    command never leases, so a real Hive Stand read is cheap and needs no mocking).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.cells for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app

runner = CliRunner()


def test_list_command_prints_the_hive_stands_one_cell(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cells", "list", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "hive_stand" in result.output
    assert "SCRATCH" in result.output  # fake_manifest's own [hive_stand] access_level default.
    assert "True" in result.output  # enabled=true.


def test_list_command_json_reports_one_row_with_the_expected_fields(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cells", "list", "--manifest", str(manifest_path), "--json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "hive_stand"
    assert row["kind"] == "REAL"
    assert row["enabled"] is True
    assert row["cores"] >= 1


def test_list_command_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = runner.invoke(app, ["cells", "list", "--manifest", str(tmp_path / "missing.toml")])

    assert result.exit_code == 2
