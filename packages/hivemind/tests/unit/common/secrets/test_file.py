"""Tests for hivemind.common.secrets.file: FileSecretStore's on-disk behaviour.

Fits into the Hive:
    Mirrors src/hivemind/common/secrets/file.py (codingrules section 3). The behaviour every
    SecretStore shares is in tests/contracts/test_secret_store_contract.py; this module covers
    what only the file store does: owner-only modes, atomic writes, the lazily created directory.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.secrets.file for the module under test.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hivemind.common.secrets import DIRECTORY_MODE, FILE_MODE, FileSecretStore

# Owner-only modes are a POSIX promise; on Windows the user profile's ACL protects the directory.
_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX-only")


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


@_POSIX_ONLY
async def test_put_creates_an_owner_only_directory_and_file(tmp_path: Path) -> None:
    root = tmp_path / "state" / "secrets"
    store = FileSecretStore(root)

    await store.put("hive.ed25519", b"key")

    assert _mode(root) == DIRECTORY_MODE
    assert _mode(root / "hive.ed25519") == FILE_MODE


@_POSIX_ONLY
async def test_put_tightens_a_directory_an_operator_made_too_open(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(mode=0o755)
    # mkdir's mode is filtered by the umask, so the loose mode the store must repair is forced.
    os.chmod(root, 0o755)  # noqa: S103 -- the too-open directory is this test's fixture

    await FileSecretStore(root).put("hive.ed25519", b"key")

    assert _mode(root) == DIRECTORY_MODE


async def test_put_leaves_only_the_secret_file_behind(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    store = FileSecretStore(root)

    await store.put("hive.ed25519", b"one")
    await store.put("hive.ed25519", b"two")

    assert sorted(entry.name for entry in root.iterdir()) == ["hive.ed25519"]
    assert (root / "hive.ed25519").read_bytes() == b"two"


async def test_a_failed_write_keeps_the_old_state_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    # A non-empty directory where the secret file should go makes the final rename fail for real.
    root = tmp_path / "secrets"
    (root / "hive.ed25519").mkdir(parents=True)
    (root / "hive.ed25519" / "occupied").write_bytes(b"x")
    store = FileSecretStore(root)

    with pytest.raises(OSError):
        await store.put("hive.ed25519", b"key")

    assert sorted(entry.name for entry in root.iterdir()) == ["hive.ed25519"]


async def test_reads_never_create_the_directory(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    store = FileSecretStore(root)

    assert await store.get("hive.ed25519") is None
    assert await store.names() == ()
    await store.delete("hive.ed25519")

    assert not root.exists()


async def test_names_skips_temporary_files_foreign_files_and_directories(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    await store.put("hive.ed25519", b"key")
    (root / ".hive.ed25519.abc.tmp").write_bytes(b"partial")
    (root / "README.TXT").write_bytes(b"not a secret this store wrote")
    (root / "nested").mkdir()

    assert await store.names() == ("hive.ed25519",)


async def test_get_reads_what_another_store_on_the_same_directory_wrote(tmp_path: Path) -> None:
    await FileSecretStore(tmp_path / "secrets").put("hive.ed25519", b"persisted")

    assert await FileSecretStore(tmp_path / "secrets").get("hive.ed25519") == b"persisted"


def test_repr_and_root_show_the_directory_only(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "secrets")

    assert store.root == tmp_path / "secrets"
    assert repr(store) == f"FileSecretStore(root={str(tmp_path / 'secrets')!r})"
