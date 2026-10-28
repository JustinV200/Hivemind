"""Test `hive init`: the starter manifest it writes, the file it never replaces, the password.

Fits into the Hive:
    Unit tests for `hivemind.cli.init` (roadmap 14.2), driven through the typer application.

Key invariants:
    - Every test writes under its own temp directory; no real Hive is touched.

See Also:
    - hivemind.cli.init.command for what is under test.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.manifest import load_manifest

runner = CliRunner()
# Strong enough for the Entrance's own strength check.
PASSWORD = "correct horse battery staple honeycomb"  # noqa: S105 -- a test's password, not a credential
LOCAL = ["init", "--provider", "ollama", "--model", "m1"]


def test_init_writes_a_manifest_with_the_hive_stand_enabled(tmp_path: Path) -> None:
    target = tmp_path / "new-hive"

    result = runner.invoke(app, [*LOCAL, "--no-password", str(target)])

    assert result.exit_code == 0, result.output
    manifest = load_manifest(target / "hive.toml")
    assert manifest.hive_stand.enabled
    assert manifest.llm.slots["queen"].model == "m1"


def test_init_never_replaces_a_manifest_without_force(tmp_path: Path) -> None:
    existing = tmp_path / "hive.toml"
    existing.write_text("# the Hive's identity\n", encoding="utf-8")

    refused = runner.invoke(app, [*LOCAL, "--no-password", str(tmp_path)])
    unchanged = existing.read_text(encoding="utf-8")
    forced = runner.invoke(app, [*LOCAL, "--no-password", "--force", str(tmp_path)])

    assert refused.exit_code == 2
    assert unchanged == "# the Hive's identity\n"
    assert forced.exit_code == 0, forced.output
    assert load_manifest(existing).hive_stand.enabled


def test_init_sets_the_operator_password_once(tmp_path: Path) -> None:
    first = runner.invoke(app, [*LOCAL, "--password-stdin", str(tmp_path)], input=f"{PASSWORD}\n")
    # A forced re-run keeps the operator: changing a password needs the current one.
    again = runner.invoke(app, [*LOCAL, "--force", "--password-stdin", str(tmp_path)], input="")

    assert first.exit_code == 0, first.output
    assert "Operator password set" in first.output
    assert again.exit_code == 0, again.output
    assert "already set" in again.output
