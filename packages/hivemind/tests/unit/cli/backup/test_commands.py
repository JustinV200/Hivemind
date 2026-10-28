"""Test `hive backup` and `hive restore` through the hive application, on temp-dir Hives only.

Fits into the Hive:
    Mirrors src/hivemind/cli/backup/commands.py and hive.py (codingrules section 3). Every Hive
    here is a `fake_manifest` under tmp_path; nothing touches a real hive.sqlite3.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import gzip
import shutil
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from typer.testing import CliRunner, Result

from hivemind.cli.app import app
from hivemind.cli.backup import RESTORED_KIND
from hivemind.cli.entrance import hold_queen_lock
from hivemind.common.secrets import HIVE_SIGNING_KEY, FileSecretStore, load_or_mint_hive_signer
from hivemind.common.sqlite import connect
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import apply_pheromone_migrations
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer


def _hive(*args: str) -> Result:
    """Run ``hive ARGS``."""
    return CliRunner().invoke(app, list(args))


def _seed(path: Path) -> tuple[HiveManifest, Path, bytes]:
    """Give a fake Hive a trail, a row, a blob and an identity key; return it, its db, the key."""
    manifest = load_manifest(path)
    db = manifest.resolve_path(manifest.hive.db)
    connection = connect(db)
    apply_pheromone_migrations(connection, SystemClock())
    connection.execute("CREATE TABLE marker (v TEXT)")
    connection.execute("INSERT INTO marker VALUES ('backed up')")
    connection.close()
    (db.parent / "basket" / "ab").mkdir(parents=True)
    (db.parent / "basket" / "ab" / "abcd").write_bytes(b"blob")
    store = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    signer = asyncio.run(load_or_mint_hive_signer(store))
    return manifest, db, signer.private_key_bytes


def _marker(db: Path) -> list[str]:
    """The marker rows, to tell a restored database from a moved-on one."""
    connection = connect(db)
    rows = [row[0] for row in connection.execute("SELECT v FROM marker")]
    connection.close()
    return rows


def test_backup_then_restore_puts_the_stores_back_and_records_it(tmp_path: Path) -> None:
    path = fake_manifest(tmp_path)
    _, db, private = _seed(path)
    archive = tmp_path / "hive.tar.gz"

    backed = _hive("backup", str(archive), "--manifest", str(path))
    # The Hive moves on: a new row, a lost blob.
    connection = connect(db)
    connection.execute("INSERT INTO marker VALUES ('later')")
    connection.close()
    (db.parent / "basket" / "ab" / "abcd").unlink()
    sha = next(line.split()[1] for line in backed.output.splitlines() if "sha256:" in line)
    refused = _hive("restore", str(archive), "--manifest", str(path))
    restored = _hive("restore", str(archive), "--manifest", str(path), "--replace", "--sha256", sha)

    assert backed.exit_code == 0, backed.output
    # The private key is in neither the archive's bytes nor the output.
    unpacked = gzip.decompress(archive.read_bytes())
    assert private not in unpacked and private.hex() not in unpacked.decode("latin-1")
    assert refused.exit_code == 1 and "--replace" in refused.output
    assert restored.exit_code == 0, restored.output
    assert _marker(db) == ["backed up"]
    assert (db.parent / "basket" / "ab" / "abcd").read_bytes() == b"blob"
    connection = connect(db)
    kinds = [row[0] for row in connection.execute("SELECT kind FROM pheromone_events")]
    connection.close()
    assert kinds == [RESTORED_KIND]


def _far(tmp_path: Path, path: Path) -> Path:
    """The same Hive's manifest on a fresh machine: its own db and an empty secret store."""
    far = tmp_path / "far"
    far.mkdir()
    db = load_manifest(path).resolve_path(load_manifest(path).hive.db)
    text = path.read_text(encoding="utf-8").replace(
        db.as_posix(), (far / "hive.sqlite3").as_posix()
    )
    (far / "hive.toml").write_text(text, encoding="utf-8")
    return far / "hive.toml"


@pytest.mark.parametrize("case", ["other hive", "no secrets", "other identity key", "queen runs"])
def test_restore_refuses_and_writes_nothing(tmp_path: Path, case: str) -> None:
    path = fake_manifest(tmp_path)
    manifest, _, _ = _seed(path)
    archive = tmp_path / "hive.tar.gz"
    _hive("backup", str(archive), "--manifest", str(path))
    target = _far(tmp_path, path)
    secrets = manifest.resolve_path(manifest.hive.secrets_dir)
    if case != "no secrets":
        shutil.copytree(secrets, target.parent / "secrets")
    if case == "other identity key":
        other = Ed25519Signer.generate().private_key_bytes
        (target.parent / "secrets" / HIVE_SIGNING_KEY).write_bytes(other)
    if case == "other hive":
        target = fake_manifest(tmp_path / "other")
    db = load_manifest(target).resolve_path(load_manifest(target).hive.db)

    if case == "queen runs":
        with hold_queen_lock(db, "a running Queen"):
            result = _hive("restore", str(archive), "--manifest", str(target))
    else:
        result = _hive("restore", str(archive), "--manifest", str(target))

    assert result.exit_code == 1
    assert "hive restore refused" in result.output, result.output
    assert not db.exists()
