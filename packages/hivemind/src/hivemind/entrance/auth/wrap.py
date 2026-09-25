"""Wrap a private key under the operator password, and open it again only with that password.

Bees (the Hive's agent processes) on the Hive Stand (the machine the Queen, the orchestrator, runs
on) run as the Hive's own operating-system user, so any key the Hive keeps on
disk in the clear, a bee can read (ADR-0041). The Hive Stand console's Ed25519 device key is one
factor of the operator's own login, so it is never stored in the clear: ``wrap_private_key``
derives a 32-byte key from the operator password with Argon2id (the password hash's profile, with
its own random salt) and seals the private key with AES-256-GCM (a random 96-bit nonce, the secret's
name as associated data, so a blob moved to another name no longer opens). The blob is
``version || salt || nonce || ciphertext-and-tag``; the version byte lets a later profile change
coexist with blobs already written. ``unwrap_private_key`` reverses it and fails with one
``KeyUnwrapError`` whatever went wrong, so a caller cannot tell a wrong password from a damaged
blob.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by
    ``hivemind.entrance.enrol.console`` (bootstrap, password change, unlock). Calls into
    ``hivemind.entrance.auth.password`` (the Argon2id derivation, off the event loop) and
    ``cryptography``'s AES-GCM.

Key invariants:
    - The plaintext key exists only in memory: nothing here writes, logs or raises it.
    - Every wrap draws a fresh salt and a fresh nonce, so wrapping the same key twice under the
      same password gives two unrelated blobs.
    - ``unwrap_private_key`` raises ``KeyUnwrapError`` with the same message for a wrong
      password, a wrong name, a truncated blob, an unknown version and a flipped bit.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for why the console
      key is wrapped.
    - hivemind.entrance.auth.password.PasswordHasher for the derivation and its semaphore.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hivemind.entrance.auth.password import SALT_BYTES, PasswordHasher
from hivemind.entrance.errors import KeyUnwrapError

WRAP_VERSION = 1  # The first byte of every blob: this module's one format so far.
NONCE_BYTES = 12  # AES-GCM's standard 96-bit nonce; random per wrap, never reused with a key.
TAG_BYTES = 16  # AES-GCM's 128-bit authentication tag, appended to the ciphertext.
_HEADER_BYTES = 1 + SALT_BYTES + NONCE_BYTES  # version || salt || nonce, before the ciphertext.

__all__ = ["NONCE_BYTES", "TAG_BYTES", "WRAP_VERSION", "unwrap_private_key", "wrap_private_key"]


async def wrap_private_key(
    hasher: PasswordHasher, password: str, name: str, private_key: bytes
) -> bytes:
    """Seal ``private_key`` under a key derived from ``password``, bound to the secret ``name``.

    Args:
        hasher: The composition root's PasswordHasher; the Argon2id derivation runs through it.
        password: The operator password the key will open with.
        name: The secret store name the blob will live under; sealed in as associated data.
        private_key: The raw private key to protect.

    Returns:
        The blob to store: version, salt, nonce, then the ciphertext and its tag.
    """
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    # Latency: one Argon2id derivation (about 0.1 s) in a worker thread.
    wrapping_key = await hasher.derive_key(password, salt)
    sealed = AESGCM(wrapping_key).encrypt(nonce, private_key, name.encode("utf-8"))
    return bytes([WRAP_VERSION]) + salt + nonce + sealed


async def unwrap_private_key(
    hasher: PasswordHasher, password: str, name: str, blob: bytes
) -> bytes:
    """Open a blob ``wrap_private_key`` made, with the password and name it was made under.

    Args:
        hasher: The composition root's PasswordHasher.
        password: The password presented.
        name: The secret store name the blob was read from.
        blob: The stored blob.

    Returns:
        The raw private key.

    Raises:
        KeyUnwrapError: The password or name is not the one the blob was sealed under, or the
            blob is malformed or altered; the message is the same in every case.
    """
    # A blob too short to hold a header and a tag, or of a version this module never wrote,
    # cannot open; it fails exactly like a wrong password so the error is never an oracle.
    if len(blob) < _HEADER_BYTES + TAG_BYTES or blob[0] != WRAP_VERSION:
        raise KeyUnwrapError(name)
    salt = blob[1 : 1 + SALT_BYTES]
    nonce = blob[1 + SALT_BYTES : _HEADER_BYTES]
    # Latency: one Argon2id derivation (about 0.1 s) in a worker thread.
    wrapping_key = await hasher.derive_key(password, salt)
    try:
        return AESGCM(wrapping_key).decrypt(nonce, blob[_HEADER_BYTES:], name.encode("utf-8"))
    except InvalidTag:
        # WHY `from None`: chaining would attach InvalidTag here and nothing to the malformed-blob
        # refusal above, and that difference alone would tell a wrong password from a bad blob.
        raise KeyUnwrapError(name) from None
