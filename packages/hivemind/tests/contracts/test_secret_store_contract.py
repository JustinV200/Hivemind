"""Contract suite for SecretStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.common.secrets.protocol.SecretStore contract and runs against
    hivemind.common.secrets.file.FileSecretStore (under tmp_path) and
    hivemind.common.secrets.fake.MemorySecretStore. A new store joins the fixture's params and
    passes here before it is used anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.secrets.protocol for the protocol under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.common.errors import SecretStoreError
from hivemind.common.secrets import (
    MAX_SECRET_BYTES,
    FileSecretStore,
    MemorySecretStore,
    SecretStore,
)

_STORE_KINDS = ("file", "memory")
_VALUE = b"\x00\x01 not really a key \xff"


@pytest.fixture(params=_STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> SecretStore:
    """A SecretStore of the parametrised kind; the file store under a directory not yet made."""
    if request.param == "file":
        return FileSecretStore(tmp_path / "state" / "secrets")
    return MemorySecretStore()


async def test_get_returns_none_for_a_secret_never_put(store: SecretStore) -> None:
    assert await store.get("hive.ed25519") is None


async def test_put_then_get_returns_the_exact_bytes(store: SecretStore) -> None:
    await store.put("hive.ed25519", _VALUE)

    assert await store.get("hive.ed25519") == _VALUE


async def test_an_empty_value_is_a_value_not_an_absence(store: SecretStore) -> None:
    await store.put("empty", b"")

    assert await store.get("empty") == b""
    assert await store.names() == ("empty",)


async def test_put_replaces_the_previous_value(store: SecretStore) -> None:
    await store.put("hive.ed25519", b"first")

    await store.put("hive.ed25519", b"second")

    assert await store.get("hive.ed25519") == b"second"


async def test_names_lists_every_secret_sorted(store: SecretStore) -> None:
    for name in ("zeta", "console.ed25519", "hive.ed25519"):
        await store.put(name, _VALUE)

    assert await store.names() == ("console.ed25519", "hive.ed25519", "zeta")


async def test_names_is_empty_for_an_empty_store(store: SecretStore) -> None:
    assert await store.names() == ()


async def test_delete_removes_a_secret(store: SecretStore) -> None:
    await store.put("hive.ed25519", _VALUE)

    await store.delete("hive.ed25519")

    assert await store.get("hive.ed25519") is None
    assert await store.names() == ()


async def test_delete_of_an_absent_secret_is_a_no_op(store: SecretStore) -> None:
    await store.delete("never-stored")

    assert await store.names() == ()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        ".hidden",
        "-flag",
        "Upper",
        "a/b",
        "a\\b",
        "a b",
        "name\n",
        "x" * 65,
        "\u00fc",
    ],
)
async def test_every_operation_refuses_an_invalid_name(store: SecretStore, name: str) -> None:
    with pytest.raises(SecretStoreError, match="invalid"):
        await store.put(name, _VALUE)
    with pytest.raises(SecretStoreError, match="invalid"):
        await store.get(name)
    with pytest.raises(SecretStoreError, match="invalid"):
        await store.delete(name)


@pytest.mark.parametrize("name", ["a", "9", "hive.ed25519", "a_b-c.d", "x" * 64])
async def test_every_name_in_the_grammar_is_accepted(store: SecretStore, name: str) -> None:
    await store.put(name, _VALUE)

    assert await store.get(name) == _VALUE


async def test_put_refuses_a_value_over_the_size_bound_and_names_only_its_length(
    store: SecretStore,
) -> None:
    with pytest.raises(SecretStoreError, match=str(MAX_SECRET_BYTES + 1)) as excinfo:
        await store.put("big", b"\x07" * (MAX_SECRET_BYTES + 1))

    assert "\x07" not in str(excinfo.value)
    assert await store.get("big") is None


async def test_put_accepts_a_value_exactly_at_the_size_bound(store: SecretStore) -> None:
    await store.put("big", b"\x07" * MAX_SECRET_BYTES)

    assert await store.get("big") == b"\x07" * MAX_SECRET_BYTES


async def test_repr_never_shows_a_value(store: SecretStore) -> None:
    await store.put("hive.ed25519", b"super-secret-bytes")

    assert "super-secret-bytes" not in repr(store)
