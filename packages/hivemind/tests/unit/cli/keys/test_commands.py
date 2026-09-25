"""Test hivemind.cli.keys.commands through the hive application, on a manifest's secret store.

Fits into the Hive:
    Mirrors src/hivemind/cli/keys/commands.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.keys import PINNED_WARNING
from hivemind.common.secrets import HIVE_SIGNING_KEY
from hivemind.manifest import load_manifest
from waggle.signing import Ed25519Signer


def _hive(path: Path, *args: str, stdin: str | None = None) -> Result:
    """Run ``hive keys ARGS --manifest path``."""
    return CliRunner().invoke(app, ["keys", *args, "--manifest", str(path)], input=stdin)


def _secrets(path: Path) -> Path:
    """The manifest's secret store directory."""
    manifest = load_manifest(path)
    return manifest.resolve_path(manifest.hive.secrets_dir)


def test_create_prints_the_public_key_and_fingerprint_never_the_private_one(
    tmp_path: Path,
) -> None:
    path = fake_manifest(tmp_path)

    created = _hive(path, "create", "relay")
    listed = _hive(path, "list", "--json")

    private = (_secrets(path) / "relay.ed25519").read_bytes()
    public = Ed25519Signer(private).public_key_bytes.hex()
    assert created.exit_code == 0, created.output
    assert f"public key: {public}" in created.output and "fingerprint: " in created.output
    assert private.hex() not in created.output and private.hex() not in listed.output
    keys = json.loads(listed.stdout)["keys"]
    assert [(key["name"], key["role"], key["public_key_hex"]) for key in keys] == [
        ("relay.ed25519", "node key", public)
    ]


def test_list_says_when_there_is_no_key_yet(tmp_path: Path) -> None:
    result = _hive(fake_manifest(tmp_path), "list")

    assert result.exit_code == 0, result.output
    assert "No Ed25519 key in the secret store yet" in result.output


def test_revoke_asks_first_then_says_what_a_pinned_peer_still_trusts(tmp_path: Path) -> None:
    path = fake_manifest(tmp_path)
    _hive(path, "create", "relay")

    kept = _hive(path, "revoke", "relay", stdin="n\n")
    revoked = _hive(path, "revoke", "relay", stdin="y\n")
    again = _hive(path, "revoke", "relay", "--yes")

    assert kept.exit_code == 1
    assert revoked.exit_code == 0, revoked.output
    assert "Revoked relay.ed25519 (fingerprint " in revoked.output
    assert " ".join(PINNED_WARNING.split()) in " ".join(revoked.output.split())
    assert not (_secrets(path) / "relay.ed25519").exists()
    assert again.exit_code == 1 and "hive keys revoke refused: No key named" in again.output


def test_the_hive_identity_key_is_refused_by_create_and_revoke(tmp_path: Path) -> None:
    path = fake_manifest(tmp_path)
    identity = Ed25519Signer.generate().private_key_bytes
    _secrets(path).mkdir(parents=True, exist_ok=True)
    (_secrets(path) / HIVE_SIGNING_KEY).write_bytes(identity)

    created = _hive(path, "create", "hive")
    revoked = _hive(path, "revoke", "hive", "--yes")
    listed = _hive(path, "list")

    assert created.exit_code == 1 and "hive keys create refused:" in created.output
    assert revoked.exit_code == 1 and "Supersedure" in revoked.output
    assert (_secrets(path) / HIVE_SIGNING_KEY).read_bytes() == identity
    assert "hive.ed25519" in listed.output and "hive identity" in listed.output
    assert identity.hex() not in listed.output
