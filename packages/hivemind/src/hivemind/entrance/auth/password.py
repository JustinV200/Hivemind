"""Hash and verify the operator's password with Argon2id, off the event loop and one at a time.

Brood 1.0 has one operator and one password, the second factor of every login at the Hive Entrance
(the Hive's one HTTP door; ADR-0041). The Entrance tables keep only its Argon2id hash, as the PHC
string ``cryptography``'s ``Argon2id`` produces (``$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>``),
so the stored value carries its own parameters and no password library is added. The parameters are
RFC 9106's second recommended profile: 3 passes over 64 MiB with 4 lanes, a 16-byte random salt and
a 32-byte output. Each derivation costs about a tenth of a second and 64 MiB, so ``PasswordHasher``
runs every one in a worker thread (codingrules section 11: CPU-bound work never blocks the loop)
behind its own semaphore of two: a burst of logins queues instead of exhausting memory or stalling
the Queen (the orchestrator). OpenSSL runs a derivation's four lanes on one thread pool shared by
the whole process (about one thread per core), and two derivations at once on a machine with fewer
than eight cores exhaust it: OpenSSL then deadlocks both, or fails one with a spurious
``MemoryError``. So every derivation in the process, whichever hasher asked for it, also takes one
module-level lock, and runs alone. The same hasher derives the key that wraps the private key of the
console on the Hive Stand (the Queen's machine) (``hivemind.entrance.auth.wrap``), because that
costs exactly as much. A password is normalised to Unicode NFKC before anything else, so the same
password typed on a phone and on a laptop (which may compose accented letters differently) hashes
the same.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. A
    ``PasswordHasher`` is built once by a composition root (the Entrance app, a CLI command, a
    test) and passed to ``hivemind.entrance.enrol.console`` and, later, the login routes. Calls
    into ``cryptography`` and ``hivemind.entrance.errors`` only.

Key invariants:
    - The semaphore belongs to the hasher its caller built, so two Entrances in one process (two
      tests) never share or starve each other's slots; the one module-level state is the lock
      that keeps OpenSSL's process-wide thread pool to one derivation at a time.
    - A password shorter than ``MIN_PASSWORD_CHARS`` or longer than ``MAX_PASSWORD_CHARS`` is never
      hashed; ``verify`` of an over-long one is False without deriving anything.
    - Verification is ``Argon2id.verify_phc_encoded`` (constant time); only ``InvalidKey`` means
      "no", anything else propagates.
    - No message, log line or error here ever carries the password or the hash.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
    - RFC 9106 section 4 for the parameter profile.
"""

from __future__ import annotations

import asyncio
import os
import threading
import unicodedata
from typing import Final

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from hivemind.entrance.errors import WeakPasswordError

ARGON2_ITERATIONS = 3  # RFC 9106 section 4, second recommended option: t = 3 passes.
ARGON2_LANES = 4  # The same profile's p = 4 lanes (parallelism).
ARGON2_MEMORY_KIB = 64 * 1024  # The same profile's m = 2^16 KiB: 64 MiB per derivation.
SALT_BYTES = 16  # RFC 9106's recommended 128-bit salt, fresh from the CSPRNG for every hash.
HASH_BYTES = 32  # A 256-bit tag: also the AES-256 key length the wrapping key needs.
MIN_PASSWORD_CHARS = 12  # Below this an offline guess against a stolen hash gets cheap.
MAX_PASSWORD_CHARS = 1024  # Longer is a mistake or an attack, never a password someone types.
DEFAULT_CONCURRENCY = 2  # ADR-0041: two derivations at once, 128 MiB ceiling, for any burst.
_NORMAL_FORM: Final = "NFKC"  # NIST SP 800-63B: normalise before hashing so devices agree.
# One derivation at a time in the whole process: OpenSSL draws every derivation's lanes from one
# process-wide thread pool, which two 4-lane derivations at once can exhaust (a deadlock, or a
# spurious MemoryError) on a machine with fewer than eight cores. Stricter than the semaphore.
_ONE_AT_A_TIME = threading.Lock()

__all__ = [
    "ARGON2_ITERATIONS",
    "ARGON2_LANES",
    "ARGON2_MEMORY_KIB",
    "DEFAULT_CONCURRENCY",
    "HASH_BYTES",
    "MAX_PASSWORD_CHARS",
    "MIN_PASSWORD_CHARS",
    "SALT_BYTES",
    "PasswordHasher",
    "check_password_strength",
]


class PasswordHasher:
    """Run Argon2id derivations in worker threads: ``concurrency`` threads, one deriving at once.

    Owns one ``asyncio.Semaphore``; like every asyncio primitive it belongs to the event loop
    that first waits on it, so build one hasher per running Entrance (or per test).
    """

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        """Build a hasher allowing ``concurrency`` derivations in flight.

        Args:
            concurrency: How many 64 MiB derivations may run at once; at least 1.

        Raises:
            ValueError: ``concurrency`` is below 1.
        """
        if concurrency < 1:
            raise ValueError(f"A PasswordHasher needs at least one slot, not {concurrency}.")
        # The one guard on this hasher's memory: every derivation below holds a slot while its
        # worker thread runs, so at most `concurrency` x 64 MiB is ever committed at once.
        self._slots = asyncio.Semaphore(concurrency)

    async def hash(self, password: str) -> str:
        """Hash a new operator password into a PHC string with a fresh random salt.

        Args:
            password: The password as typed; normalised to NFKC before its length is checked.

        Returns:
            The ``$argon2id$...`` PHC string to store in the Entrance tables.

        Raises:
            WeakPasswordError: The password is shorter than ``MIN_PASSWORD_CHARS`` or longer
                than ``MAX_PASSWORD_CHARS`` characters.
        """
        normalised = _normalise(password)
        check_password_strength(normalised)
        # Latency: about 0.1 s of CPU in a worker thread, after a free slot and the process's turn.
        async with self._slots:
            return await asyncio.to_thread(_derive_phc, normalised)

    async def verify(self, password: str, encoded: str) -> bool:
        """Return whether ``password`` matches the stored PHC string, in constant time.

        Args:
            password: The password presented; normalised to NFKC like at hashing time.
            encoded: The PHC string from the Entrance tables.

        Returns:
            True when it matches; False when it does not, when the password is longer than any
            password ever hashed, or when ``encoded`` is not an Argon2id PHC string at all.
        """
        normalised = _normalise(password)
        # Nothing longer than the bound was ever hashed, so it cannot match; refusing before the
        # derivation keeps a flood of huge passwords from costing a thread each.
        if len(normalised) > MAX_PASSWORD_CHARS:
            return False
        # Latency: about 0.1 s of CPU in a worker thread, after a free slot and the process's turn.
        async with self._slots:
            return await asyncio.to_thread(_matches_phc, normalised, encoded)

    async def derive_key(self, password: str, salt: bytes) -> bytes:
        """Derive a 32-byte key from ``password`` and ``salt`` with the same Argon2id profile.

        Used to wrap the console's private key under the operator password; the salt is stored
        beside the wrapped key, so the same password and salt always give the same key.

        Args:
            password: The password; normalised to NFKC.
            salt: Exactly ``SALT_BYTES`` random bytes.

        Returns:
            ``HASH_BYTES`` bytes of key material.

        Raises:
            ValueError: ``salt`` is not ``SALT_BYTES`` long.
        """
        if len(salt) != SALT_BYTES:
            raise ValueError(f"An Argon2id salt must be {SALT_BYTES} bytes, not {len(salt)}.")
        normalised = _normalise(password)
        # Latency: about 0.1 s of CPU in a worker thread, after a free slot and the process's turn.
        async with self._slots:
            return await asyncio.to_thread(_derive_raw, normalised, salt)


def check_password_strength(password: str) -> None:
    """Refuse a password that is too short or too long to become the operator password.

    Callers that write more than a hash (the console bootstrap) call this before writing
    anything, so a weak password never leaves a half-initialised Hive behind.

    Args:
        password: The candidate, already NFKC-normalised or as typed (normalised here too).

    Raises:
        WeakPasswordError: Fewer than ``MIN_PASSWORD_CHARS`` or more than ``MAX_PASSWORD_CHARS``
            characters after normalisation.
    """
    # The rule, never the password or its length, goes into the message.
    if not MIN_PASSWORD_CHARS <= len(_normalise(password)) <= MAX_PASSWORD_CHARS:
        raise WeakPasswordError(
            f"The operator password must be between {MIN_PASSWORD_CHARS} and "
            f"{MAX_PASSWORD_CHARS} characters long."
        )


def _normalise(password: str) -> str:
    """Return ``password`` in Unicode NFKC, the one form every derivation sees."""
    return unicodedata.normalize(_NORMAL_FORM, password)


def _argon2id(salt: bytes) -> Argon2id:
    """Build an Argon2id instance with this module's profile over ``salt`` (single use)."""
    return Argon2id(
        salt=salt,
        length=HASH_BYTES,
        iterations=ARGON2_ITERATIONS,
        lanes=ARGON2_LANES,
        memory_cost=ARGON2_MEMORY_KIB,
    )


def _derive_phc(password: str) -> str:
    """Hash ``password`` under a fresh salt into a PHC string; blocking, run in a thread."""
    with _ONE_AT_A_TIME:
        return _argon2id(os.urandom(SALT_BYTES)).derive_phc_encoded(password.encode("utf-8"))


def _matches_phc(password: str, encoded: str) -> bool:
    """Check ``password`` against a PHC string; blocking, run in a thread."""
    # verify_phc_encoded reads the parameters from the string itself and compares in constant
    # time; InvalidKey covers both a wrong password and a string that is not a PHC string.
    try:
        with _ONE_AT_A_TIME:
            Argon2id.verify_phc_encoded(password.encode("utf-8"), encoded)
    except InvalidKey:
        return False
    return True


def _derive_raw(password: str, salt: bytes) -> bytes:
    """Derive raw key material from ``password`` and ``salt``; blocking, run in a thread."""
    with _ONE_AT_A_TIME:
        return _argon2id(salt).derive(password.encode("utf-8"))
