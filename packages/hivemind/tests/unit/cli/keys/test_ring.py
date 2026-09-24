"""Test hivemind.cli.keys.ring: node keys minted and revoked by name, the Hive's own refused.

Fits into the Hive:
    Mirrors src/hivemind/cli/keys/ring.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from hivemind.cli.keys import (
    CONSOLE_DEVICE,
    HIVE_IDENTITY,
    NODE_KEY,
    KeyRingError,
    create_key,
    list_keys,
    revoke_key,
)
from hivemind.cli.keys.ring import UNREADABLE, KeyNotFoundError
from hivemind.common.secrets import HIVE_SIGNING_KEY, FileSecretStore
from hivemind.entrance.auth import key_fingerprint
from hivemind.entrance.enrol import CONSOLE_KEY_NAME
from waggle.signing import Ed25519Signer, public_key_hex


async def test_a_minted_node_key_shows_only_its_public_half(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)

    with capture_logs() as logs:
        entry = await create_key(store, "relay")
    stored = await store.get("relay.ed25519")

    assert stored is not None
    public = Ed25519Signer(stored).public_key_bytes
    assert (entry.name, entry.role) == ("relay.ed25519", NODE_KEY)
    assert entry.public_key_hex == public_key_hex(public)
    assert entry.fingerprint == key_fingerprint(public)
    assert stored.hex() not in repr(entry) and stored.hex() not in repr(logs)
    assert logs[0]["event"] == "keys.created" and logs[0]["fingerprint"] == entry.fingerprint


async def test_a_name_already_taken_is_refused_and_the_key_kept(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    await create_key(store, "relay")
    before = await store.get("relay.ed25519")

    with pytest.raises(KeyRingError, match="already exists"):
        await create_key(store, "relay.ed25519")

    assert await store.get("relay.ed25519") == before


@pytest.mark.parametrize("name", ["hive", HIVE_SIGNING_KEY, "console", CONSOLE_KEY_NAME])
async def test_the_hives_own_keys_are_never_minted_by_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(KeyRingError, match="minted by the Hive"):
        await create_key(FileSecretStore(tmp_path), name)


@pytest.mark.parametrize("name", ["Relay", "../escape", "", "-lead", "a" * 57, "with space"])
async def test_a_name_that_is_not_a_key_name_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(KeyRingError, match="is not a key name"):
        await create_key(FileSecretStore(tmp_path), name)


async def test_revoking_deletes_the_private_half_and_names_the_key(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    created = await create_key(store, "relay")

    with capture_logs() as logs:
        revoked = await revoke_key(store, "relay")

    assert revoked == created
    assert await store.get("relay.ed25519") is None
    assert logs[0]["event"] == "keys.revoked" and logs[0]["fingerprint"] == created.fingerprint
    with pytest.raises(KeyNotFoundError, match=r"No key named relay\.ed25519"):
        await revoke_key(store, "relay")


async def test_the_hive_identity_and_the_console_key_are_never_revoked(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    identity = Ed25519Signer.generate().private_key_bytes
    await store.put(HIVE_SIGNING_KEY, identity)
    await store.put(CONSOLE_KEY_NAME, b"wrapped")

    with pytest.raises(KeyRingError, match="Supersedure"):
        await revoke_key(store, "hive")
    with pytest.raises(KeyRingError, match="operator password --reset"):
        await revoke_key(store, "console.ed25519")

    assert await store.get(HIVE_SIGNING_KEY) == identity
    assert await store.get(CONSOLE_KEY_NAME) == b"wrapped"


async def test_the_list_shows_every_key_by_role_and_leaves_other_secrets_out(
    tmp_path: Path,
) -> None:
    store = FileSecretStore(tmp_path)
    identity = Ed25519Signer.generate()
    await store.put(HIVE_SIGNING_KEY, identity.private_key_bytes)
    await store.put(CONSOLE_KEY_NAME, b"wrapped under the password")
    await store.put("broken.ed25519", b"short")
    await store.put("provider_token", b"not a key")
    relay = await create_key(store, "relay")

    entries = await list_keys(store)

    assert [(entry.name, entry.role) for entry in entries] == [
        ("broken.ed25519", UNREADABLE),
        (CONSOLE_KEY_NAME, CONSOLE_DEVICE),
        (HIVE_SIGNING_KEY, HIVE_IDENTITY),
        ("relay.ed25519", NODE_KEY),
    ]
    assert entries[0].public_key_hex is None and entries[1].fingerprint is None
    assert entries[2].fingerprint == key_fingerprint(identity.public_key_bytes)
    assert entries[3] == relay
