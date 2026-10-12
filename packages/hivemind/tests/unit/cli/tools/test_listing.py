"""Tests for hivemind.cli.tools.listing: `hive tools list` and `hive tools show`.

Fits into the Hive:
    Mirrors src/hivemind/cli/tools/listing.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`), with tools promoted through
    the real Comb Registry.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.tools.listing for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

from unit.cli.tools.harness import invoke, make_hive, promote

from waggle.messages.tool.authoring import ToolScope


def test_list_says_so_when_nothing_was_ever_promoted(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "list")

    assert result.exit_code == 0, result.output
    assert "no tools in the Comb Registry" in result.stdout


def test_list_prints_a_promoted_tool_with_its_short_hash(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    _, change = promote(manifest)

    result = invoke(manifest, "list")

    assert result.exit_code == 0, result.output
    header, line = result.stdout.splitlines()[:2]
    assert header.split()[:6] == ["NAME", "VERSION", "SCOPE", "CELL", "STATE", "CURRENT"]
    assert line.split()[:6] == ["echo_tool", "1.0.0", "HIVE", "-", "PROMOTED", "yes"]
    assert change.row.package_sha256[:12] in line
    assert change.row.promoted_at.isoformat(timespec="seconds") in line


def test_list_shows_only_the_current_version_unless_all_is_given(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest, version="1.0.0")
    promote(manifest, version="1.1.0")

    current = invoke(manifest, "list")
    every = invoke(manifest, "list", "--all")

    assert "1.1.0" in current.stdout and "1.0.0" not in current.stdout
    assert "1.1.0" in every.stdout and "1.0.0" in every.stdout


def test_list_narrows_by_scope_and_cell(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest, name="hive_tool")
    cell_spec, _ = promote(manifest, name="cell_tool", scope=ToolScope.CELL)

    by_scope = invoke(manifest, "list", "--scope", "cell")
    by_cell = invoke(manifest, "list", "--cell", str(cell_spec.cell_id))

    for result in (by_scope, by_cell):
        assert result.exit_code == 0, result.output
        assert "cell_tool" in result.stdout and "hive_tool" not in result.stdout
    assert str(cell_spec.cell_id) in by_scope.stdout


def test_list_json_is_one_document_with_every_row(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    _, change = promote(manifest)

    result = invoke(manifest, "list", "--json")

    document = json.loads(result.stdout)
    (tool,) = document["tools"]
    assert tool["name"] == "echo_tool" and tool["current"] is True
    assert tool["package_sha256"] == change.row.package_sha256[:12]


def test_list_refuses_a_cell_that_is_not_a_cell_id(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "list", "--cell", "not-a-cell")

    assert result.exit_code == 2
    assert "is not a Cell id" in result.output


def test_show_prints_the_spec_the_report_summary_and_the_package(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    spec, change = promote(manifest, required_capabilities=("net:example.org", "fs:read:**"))

    result = invoke(manifest, "show", "echo_tool")

    assert result.exit_code == 0, result.output
    assert f"description: {spec.description}" in result.stdout
    assert f"input schema: {spec.input_schema_json}" in result.stdout
    assert "capabilities: net:example.org, fs:read:**" in result.stdout
    assert "supported os: LINUX" in result.stdout
    assert "report: path SANDBOX_CELL, 6/6 gates passed, failing gate -" in result.stdout
    assert f"package_sha256: {change.row.package_sha256}" in result.stdout
    assert f"package_size: {change.row.package_size}" in result.stdout


def test_show_names_a_version_and_a_cell_scope_tool(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest, version="1.0.0", description="The first echo.")
    promote(manifest, version="1.1.0", description="The second echo.")
    cell_spec, _ = promote(manifest, name="cell_tool", scope=ToolScope.CELL)

    old = invoke(manifest, "show", "echo_tool", "--version", "1.0.0")
    cell = invoke(manifest, "show", "cell_tool", "--cell", str(cell_spec.cell_id))

    assert "The first echo." in old.stdout
    assert cell.exit_code == 0, cell.output
    assert f"cell: {cell_spec.cell_id}" in cell.stdout


def test_show_json_carries_the_stored_spec_and_report(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    spec, _ = promote(manifest)

    result = invoke(manifest, "show", "echo_tool", "--json")

    document = json.loads(result.stdout)
    assert document["spec"]["name"] == "echo_tool"
    assert document["spec"]["description"] == spec.description
    assert document["report"]["passed"] is True


def test_show_of_an_unknown_tool_exits_1_with_the_registrys_code(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "show", "nothing_here")

    assert result.exit_code == 1
    assert result.stderr.startswith("hivemind.royal_jelly.tool_not_found")
