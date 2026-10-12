"""Tests for hivemind.cli.tools.lifecycle: `hive tools retire` and `hive tools rollback`.

Fits into the Hive:
    Mirrors src/hivemind/cli/tools/lifecycle.py (codingrules section 3). Drives the real typer app
    against a real manifest and SQLite file (`harness`); each success is read back through the real
    registry and trail, and each refusal must leave both exactly as they were.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.tools.lifecycle for the module under test.
    - hivemind.royal_jelly.registry for the refusals these commands surface.
"""

from __future__ import annotations

from pathlib import Path

from unit.cli.tools.harness import invoke, make_hive, promote, rows, trail_events

from hivemind.royal_jelly import ToolState
from waggle.messages.tool.authoring import ToolScope


def test_retire_marks_the_version_retired_and_records_it_as_the_human(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    _, change = promote(manifest)

    result = invoke(manifest, "retire", "echo_tool", "--version", "1.0.0", "--reason", "Obsolete.")

    assert result.exit_code == 0, result.output
    assert "retired echo_tool 1.0.0 (HIVE) as queen" in result.stdout
    assert "reason: Obsolete." in result.stdout
    (row,) = rows(manifest)
    assert row.state is ToolState.RETIRED and not row.is_current
    (event,) = trail_events(manifest, "tool.retired")
    assert event.actor == "human" and event.subject_id == change.row.tool_id
    assert event.payload["version"] == "1.0.0" and event.payload["authority"] == "queen"
    assert event.payload["package_sha256"] == change.row.package_sha256[:16]


def test_retire_of_a_cell_scope_tool_names_its_cell(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    spec, _ = promote(manifest, scope=ToolScope.CELL)

    result = invoke(
        manifest,
        *("retire", "echo_tool", "--version", "1.0.0", "--reason", "Gone.", "--cell"),
        str(spec.cell_id),
    )

    assert result.exit_code == 0, result.output
    assert f"(CELL {spec.cell_id})" in result.stdout
    assert [row.state for row in rows(manifest)] == [ToolState.RETIRED]


def test_retire_of_a_version_that_does_not_exist_exits_1_and_records_nothing(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest)

    result = invoke(manifest, "retire", "echo_tool", "--version", "9.9.9", "--reason", "Typo.")

    assert result.exit_code == 1
    assert result.stderr.startswith("hivemind.royal_jelly.tool_not_found")
    assert [row.state for row in rows(manifest)] == [ToolState.PROMOTED]
    assert trail_events(manifest, "tool.retired") == []


def test_retiring_a_version_twice_is_refused_with_the_transition_code(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest)
    args = ("retire", "echo_tool", "--version", "1.0.0", "--reason", "Again.")
    invoke(manifest, *args)

    result = invoke(manifest, *args)

    assert result.exit_code == 1
    assert result.stderr.startswith("hivemind.royal_jelly.tool_transition")
    assert len(trail_events(manifest, "tool.retired")) == 1


def test_retire_with_a_blank_reason_is_bad_input(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest)

    result = invoke(manifest, "retire", "echo_tool", "--version", "1.0.0", "--reason", "  ")

    assert result.exit_code == 2
    assert "reason" in result.stderr
    assert trail_events(manifest, "tool.retired") == []


def test_rollback_makes_the_older_version_current_and_records_both_events(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest, version="1.0.0")
    promote(manifest, version="1.1.0")

    result = invoke(manifest, "rollback", "echo_tool", "--to", "1.0.0", "--reason", "1.1.0 broke.")

    assert result.exit_code == 0, result.output
    assert "rolled back to echo_tool 1.0.0 (HIVE) as queen" in result.stdout
    assert "displaced version: 1.1.0" in result.stdout
    current = {row.version: row.is_current for row in rows(manifest)}
    assert current == {"1.0.0": True, "1.1.0": False}
    (promoted,) = [e for e in trail_events(manifest, "tool.promoted") if e.actor == "human"]
    (rolled_back,) = trail_events(manifest, "tool.rolled_back")
    assert promoted.payload["supersedes_version"] == "1.1.0"
    assert rolled_back.actor == "human" and rolled_back.payload["version"] == "1.0.0"
    assert rolled_back.payload["reason"] == "1.1.0 broke."


def test_rollback_to_the_current_version_is_refused_with_its_code(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest, version="1.0.0")
    promote(manifest, version="1.1.0")

    result = invoke(manifest, "rollback", "echo_tool", "--to", "1.1.0", "--reason", "No.")

    assert result.exit_code == 1
    assert result.stderr.startswith("hivemind.royal_jelly.rollback_refused")
    assert trail_events(manifest, "tool.rolled_back") == []
    assert {row.version: row.is_current for row in rows(manifest)}["1.1.0"] is True


def test_rollback_to_a_retired_version_is_refused(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    promote(manifest, version="1.0.0")
    promote(manifest, version="1.1.0")
    invoke(manifest, "retire", "echo_tool", "--version", "1.0.0", "--reason", "Bad.")

    result = invoke(manifest, "rollback", "echo_tool", "--to", "1.0.0", "--reason", "Retry.")

    assert result.exit_code == 1
    assert "rollback_refused" in result.stderr
    assert trail_events(manifest, "tool.rolled_back") == []
