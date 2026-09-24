"""Tests for hivemind.entrance.auth.wrap: a private key sealed under the operator password.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/wrap.py (codingrules section 3). Each wrap and each unwrap
    runs one real Argon2id derivation, so the refusals share one wrapped blob.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.wrap for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.auth.password import SALT_BYTES, PasswordHasher
from hivemind.entrance.auth.wrap import (
    NONCE_BYTES,
    TAG_BYTES,
    WRAP_VERSION,
    unwrap_private_key,
    wrap_private_key,
)
from hivemind.entrance.errors import KeyUnwrapError

_PASSWORD = "correct horse battery staple"  # noqa: S105 -- a test's password, not a credential
_NAME = "console.ed25519"
_KEY = bytes(range(32))


@pytest.fixture
def hasher() -> PasswordHasher:
    """A hasher per test: its semaphore belongs to the event loop of the test that uses it."""
    return PasswordHasher()


async def test_unwrap_returns_the_key_wrap_sealed(hasher: PasswordHasher) -> None:
    blob = await wrap_private_key(hasher, _PASSWORD, _NAME, _KEY)

    assert await unwrap_private_key(hasher, _PASSWORD, _NAME, blob) == _KEY


async def test_the_blob_is_version_salt_nonce_ciphertext_and_never_the_key(
    hasher: PasswordHasher,
) -> None:
    blob = await wrap_private_key(hasher, _PASSWORD, _NAME, _KEY)

    assert blob[0] == WRAP_VERSION
    assert len(blob) == 1 + SALT_BYTES + NONCE_BYTES + len(_KEY) + TAG_BYTES
    assert _KEY not in blob


async def test_wrapping_twice_gives_unrelated_blobs(hasher: PasswordHasher) -> None:
    first = await wrap_private_key(hasher, _PASSWORD, _NAME, _KEY)
    second = await wrap_private_key(hasher, _PASSWORD, _NAME, _KEY)

    assert first[1:] != second[1:]


async def test_every_failure_reads_the_same_and_carries_no_cause(hasher: PasswordHasher) -> None:
    blob = await wrap_private_key(hasher, _PASSWORD, _NAME, _KEY)
    flipped = blob[:-1] + bytes([blob[-1] ^ 0x01])
    attempts = [
        ("wrong password", "not the password at all", _NAME, blob),
        ("wrong name", _PASSWORD, "hive.ed25519", blob),
        ("flipped bit", _PASSWORD, _NAME, flipped),
        ("truncated", _PASSWORD, _NAME, blob[:20]),
        ("unknown version", _PASSWORD, _NAME, bytes([WRAP_VERSION + 1]) + blob[1:]),
    ]
    messages = set()

    for label, password, name, candidate in attempts:
        with pytest.raises(KeyUnwrapError) as excinfo:
            await unwrap_private_key(hasher, password, name, candidate)
        # Chaining InvalidTag would tell a wrong password from a malformed blob.
        assert excinfo.value.__cause__ is None, label
        messages.add(str(excinfo.value).replace(repr(name), "NAME"))

    assert messages == {"The wrapped key NAME could not be opened with this password."}
