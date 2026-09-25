"""Test hivemind.cli.entrance.operators: the operator password set, changed and reset, and add.

Real Argon2id and the Hive's own SQLite file and secret store, through the ``hive`` application,
the way the operator types it at the Hive Stand; the reset's refusal beside a running serve is the
serve lock held by the test itself.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/operators.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.entrance.auth import PASSWORD
from builders.entrance.stand import stand_manifest
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.entrance import hold_serve_lock
from hivemind.common.secrets import FileSecretStore
from hivemind.entrance.auth import PasswordHasher
from hivemind.entrance.enrol import unlock_console_key
from hivemind.entrance.errors import KeyUnwrapError
from hivemind.manifest import HiveManifest, load_manifest

runner = CliRunner()

_NEW_PASSWORD = "a longer and entirely new password"  # noqa: S105 -- a test's password


def _password(path: Path, *flags: str, stdin: str) -> Result:
    """Run ``hive entrance operator password`` with ``flags``, reading passwords from stdin."""
    args = ["entrance", "operator", "password", "--manifest", str(path), "--password-stdin"]
    return runner.invoke(app, [*args, *flags], input=stdin)


def _opens(manifest: HiveManifest, password: str) -> bool:
    """Whether ``password`` opens the console key in the Hive's secret store."""
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    try:
        asyncio.run(unlock_console_key(secrets, PasswordHasher(), password))
    except KeyUnwrapError:
        return False
    return True


def test_the_first_password_sets_up_the_operator_and_the_console(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)

    result = _password(path, stdin=f"{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert "Operator password set; the Hive Stand console is device_" in result.output
    assert "key fingerprint" in result.output and PASSWORD not in result.output
    assert _opens(load_manifest(path, {}), PASSWORD)


def test_a_change_needs_the_current_password_then_takes_the_new_one(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _password(path, stdin=f"{PASSWORD}\n")

    changed = _password(path, stdin=f"{PASSWORD}\n{_NEW_PASSWORD}\n")

    assert changed.exit_code == 0, changed.output
    manifest = load_manifest(path, {})
    assert _opens(manifest, _NEW_PASSWORD) and not _opens(manifest, PASSWORD)


def test_a_change_with_the_wrong_current_password_changes_nothing(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _password(path, stdin=f"{PASSWORD}\n")

    refused = _password(path, stdin=f"not the password at all\n{_NEW_PASSWORD}\n")

    assert refused.exit_code == 1
    assert "operator password refused" in refused.output and "not correct" in refused.output
    assert _opens(load_manifest(path, {}), PASSWORD)


def test_a_weak_first_password_is_refused_and_nothing_is_set(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)

    refused = _password(path, stdin="short\n")

    assert refused.exit_code == 1
    assert not _opens_any(load_manifest(path, {}))


def test_a_reset_refuses_while_hive_serve_holds_the_hive(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _password(path, stdin=f"{PASSWORD}\n")
    manifest = load_manifest(path, {})

    with hold_serve_lock(manifest.resolve_path(manifest.hive.db), "hive serve"):
        refused = _password(path, "--reset", "--yes", stdin=f"{_NEW_PASSWORD}\n")

    assert refused.exit_code == 1
    assert "in use by another process" in refused.output and "hive serve" in refused.output
    assert _opens(manifest, PASSWORD)


def test_a_reset_with_serve_stopped_seals_a_new_console_under_the_new_password(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path)
    _password(path, stdin=f"{PASSWORD}\n")

    reset = _password(path, "--reset", "--yes", stdin=f"{_NEW_PASSWORD}\n")

    assert reset.exit_code == 0, reset.output
    assert "revokes every enrolled device" in reset.output
    assert "Every device was revoked; the new Hive Stand console is device_" in reset.output
    manifest = load_manifest(path, {})
    assert _opens(manifest, _NEW_PASSWORD) and not _opens(manifest, PASSWORD)


def test_a_reset_the_operator_does_not_confirm_resets_nothing(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    _password(path, stdin=f"{PASSWORD}\n")

    declined = runner.invoke(
        app,
        ["entrance", "operator", "password", "--reset", "--manifest", str(path)],
        input="n\n",
    )

    assert declined.exit_code == 1
    assert "Nothing was reset." in declined.output
    assert _opens(load_manifest(path, {}), PASSWORD)


@pytest.mark.parametrize(
    ("operators", "reason"),
    [(1, "single-operator"), (2, "keeps a single operator credential")],
)
def test_operator_add_is_refused_whatever_the_manifest_says(
    tmp_path: Path, operators: int, reason: str
) -> None:
    path = stand_manifest(tmp_path, f"operators = {operators}\n")

    result = runner.invoke(app, ["entrance", "operator", "add", "--manifest", str(path)])

    assert result.exit_code == 1
    assert reason in result.output


def _opens_any(manifest: HiveManifest) -> bool:
    """Whether any console key exists at all in the Hive's secret store."""
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    return "console.ed25519" in asyncio.run(secrets.names())
