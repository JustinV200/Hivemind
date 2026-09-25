"""Define SecretStore, the protocol every secret store implements, and the name and size rules.

A secret here is key material the Hive itself holds and must never show anyone: the Hive's own
Ed25519 signing key, the device key of the console on the Hive Stand (the machine the Hive's
orchestrator runs on), and later keys of the same kind.
Codingrules section 13 keeps every such value out of the manifest, the code, the logs and the
Pheromone Trail (the Hive's audit log); a `SecretStore` is where it lives instead, addressed by a
short, portable name. The protocol is deliberately tiny (get, put, delete, list names) so an OS
keyring or a hardware store can implement it later without the callers noticing, and the two
rules every implementation shares, what a name may look like and how large a value may be, live
here beside it so no implementation can drift from the other.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Implemented by
    ``hivemind.common.secrets.file.FileSecretStore`` (one owner-only file per secret) and
    ``hivemind.common.secrets.fake.MemorySecretStore`` (tests and demos). Read and written by
    ``hivemind.common.secrets.signers`` (the Hive's signing key, for the Queen) and by
    ``hivemind.entrance.enrol.console`` (the Hive Stand console's device key).

Key invariants:
    - A valid name matches ``SECRET_NAME_PATTERN`` in full: 1 to 64 characters of ``[a-z0-9_.-]``
      starting with a letter or digit, so no name can be ``.``, ``..``, a hidden file or an
      option-looking ``-flag``, and every name is a safe file name on Linux, macOS and Windows.
    - A value is at most ``MAX_SECRET_BYTES`` long; an empty value is a value, distinct from an
      absent secret (``get`` returns ``b""`` for one and ``None`` for the other).
    - Nothing here, and nothing an implementation raises, ever carries a secret's value: errors
      name the secret and the store only (codingrules sections 12 and 13).

See Also:
    - .claude/codingrules.md section 13 for "secrets come from environment variables or a secret
      store adapter" and Appendix C for the Hive identity row this store holds.
    - hivemind.common.secrets.file for the durable implementation.
    - packages/hivemind/tests/contracts/test_secret_store_contract.py for the shared contract.
"""

from __future__ import annotations

import re
from typing import Protocol

from hivemind.common.errors import SecretStoreError

# A letter or digit first, then up to 63 of letters, digits, `_`, `.` and `-`: portable as a file
# name everywhere and never `.`, `..`, a dotfile (FileSecretStore's temporary files start with a
# dot, so no real name can collide with one) or something a shell would read as an option.
SECRET_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
# Key material is tens of bytes; 64 KiB leaves room for a certificate chain or a PEM bundle and
# still bounds what a buggy caller can write to disk under an owner-only directory.
MAX_SECRET_BYTES = 64 * 1024

__all__ = [
    "MAX_SECRET_BYTES",
    "SECRET_NAME_PATTERN",
    "SecretStore",
    "check_secret_name",
    "check_secret_value",
]


class SecretStore(Protocol):
    """Hold named secrets (raw bytes) for the Hive, and never reveal them anywhere but ``get``.

    Implementations must be safe to call concurrently from one event loop, must never log or
    ``repr`` a value, and must validate every name with ``check_secret_name`` and every value with
    ``check_secret_value`` before touching their backing, so a bad name is refused identically by
    every implementation (the contract suite asserts it).
    """

    async def get(self, name: str) -> bytes | None:
        """Return the secret stored under ``name``, or None when there is none.

        Args:
            name: The secret's name; must match ``SECRET_NAME_PATTERN``.

        Returns:
            The exact bytes last ``put`` under ``name``, or None if nothing is stored there.

        Raises:
            SecretStoreError: ``name`` is not a valid secret name.
        """
        ...

    async def put(self, name: str, value: bytes) -> None:
        """Store ``value`` under ``name``, replacing any secret already there, atomically.

        A reader never observes a half-written value: it sees the old secret or the new one.

        Args:
            name: The secret's name; must match ``SECRET_NAME_PATTERN``.
            value: The raw secret, at most ``MAX_SECRET_BYTES`` long.

        Raises:
            SecretStoreError: ``name`` is not a valid secret name, or ``value`` is too large.
        """
        ...

    async def delete(self, name: str) -> None:
        """Remove the secret stored under ``name``; removing an absent secret is a no-op.

        Idempotent so a caller retrying after a partial failure (a revoked key being purged,
        say) never has to check first.

        Args:
            name: The secret's name; must match ``SECRET_NAME_PATTERN``.

        Raises:
            SecretStoreError: ``name`` is not a valid secret name.
        """
        ...

    async def names(self) -> tuple[str, ...]:
        """Return the name of every secret currently stored, sorted.

        Returns:
            Every stored secret's name, ascending; empty when the store holds nothing (or its
            backing does not exist yet). Names are not secret; values never appear here.
        """
        ...


def check_secret_name(name: str) -> None:
    """Refuse a secret name outside ``SECRET_NAME_PATTERN``.

    Every implementation calls this first, so a name is either valid everywhere or nowhere.

    Args:
        name: The candidate name.

    Raises:
        SecretStoreError: ``name`` does not match ``SECRET_NAME_PATTERN`` in full.
    """
    # fullmatch, not match: `$` in a match() pattern would accept a trailing newline, and a name
    # carrying one would reach the file system as a different file than the caller asked for.
    if SECRET_NAME_PATTERN.fullmatch(name) is None:
        raise SecretStoreError(
            f"Secret name {name!r} is invalid: use 1 to 64 characters of a-z, 0-9, '_', '.' or "
            "'-', starting with a letter or digit."
        )


def check_secret_value(value: bytes) -> None:
    """Refuse a secret value larger than ``MAX_SECRET_BYTES``.

    Args:
        value: The candidate value; only its length is read, and only its length is reported.

    Raises:
        SecretStoreError: ``value`` is longer than ``MAX_SECRET_BYTES``.
    """
    # The length, never the bytes, goes into the message: the value is the secret itself.
    if len(value) > MAX_SECRET_BYTES:
        raise SecretStoreError(
            f"A secret may hold at most {MAX_SECRET_BYTES} bytes; this one holds {len(value)}."
        )
