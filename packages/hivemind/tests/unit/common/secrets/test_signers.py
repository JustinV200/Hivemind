"""Tests for hivemind.common.secrets.signers: the persisted Ed25519 signing keys.

Fits into the Hive:
    Mirrors src/hivemind/common/secrets/signers.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.secrets.signers for the module under test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hivemind.common.errors import SecretStoreError
from hivemind.common.secrets import (
    HIVE_SIGNING_KEY,
    FileSecretStore,
    MemorySecretStore,
    load_or_mint_hive_signer,
    load_or_mint_signer,
)
from waggle.signing import PRIVATE_KEY_BYTES, Ed25519Signer


async def test_the_first_call_mints_and_persists_the_hive_key() -> None:
    store = MemorySecretStore()

    signer = await load_or_mint_hive_signer(store)

    assert HIVE_SIGNING_KEY == "hive.ed25519"
    assert await store.get(HIVE_SIGNING_KEY) == signer.private_key_bytes


async def test_later_calls_return_the_same_key_across_stores_on_one_directory(
    tmp_path: Path,
) -> None:
    first = await load_or_mint_hive_signer(FileSecretStore(tmp_path / "secrets"))

    again = await load_or_mint_hive_signer(FileSecretStore(tmp_path / "secrets"))

    assert again.public_key_bytes == first.public_key_bytes


async def test_an_existing_key_is_used_as_is_never_replaced() -> None:
    store = MemorySecretStore()
    existing = Ed25519Signer.generate()
    await store.put("node.ed25519", existing.private_key_bytes)

    signer = await load_or_mint_signer(store, "node.ed25519")

    assert signer.public_key_bytes == existing.public_key_bytes
    assert await store.names() == ("node.ed25519",)


async def test_a_corrupt_key_is_refused_naming_the_secret_but_never_its_bytes() -> None:
    store = MemorySecretStore()
    corrupt = b"\x42" * (PRIVATE_KEY_BYTES - 1)
    await store.put(HIVE_SIGNING_KEY, corrupt)

    with pytest.raises(SecretStoreError, match=re.escape("'hive.ed25519'")) as excinfo:
        await load_or_mint_hive_signer(store)

    assert corrupt.hex() not in str(excinfo.value)
    assert await store.get(HIVE_SIGNING_KEY) == corrupt  # Refused, never minted over.
