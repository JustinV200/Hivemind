"""Tests for hivemind.entrance.auth.password: Argon2id hashing behind a bounded semaphore.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/password.py (codingrules section 3). Every derivation here
    is the real 64 MiB Argon2id, about a tenth of a second each, so the tests keep their count low.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.password for the module under test.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from hivemind.entrance.auth import password as password_module
from hivemind.entrance.auth.password import (
    MAX_PASSWORD_CHARS,
    MIN_PASSWORD_CHARS,
    SALT_BYTES,
    PasswordHasher,
    check_password_strength,
)
from hivemind.entrance.errors import WeakPasswordError

_PASSWORD = "correct horse battery staple"  # noqa: S105 -- a test's password, not a credential
_RELEASE_TIMEOUT_S = 5.0  # Bounds a blocked worker thread if the test itself fails midway.
_CONCURRENT_TIMEOUT_S = 30.0  # Four real derivations in turn take about a second.


async def test_hash_writes_an_argon2id_phc_string_with_the_rfc_9106_profile() -> None:
    encoded = await PasswordHasher().hash(_PASSWORD)

    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert _PASSWORD not in encoded


async def test_verify_accepts_the_password_and_refuses_any_other() -> None:
    hasher = PasswordHasher()
    encoded = await hasher.hash(_PASSWORD)

    assert await hasher.verify(_PASSWORD, encoded) is True
    assert await hasher.verify(_PASSWORD + "!", encoded) is False


async def test_two_hashes_of_one_password_differ_by_their_salt() -> None:
    hasher = PasswordHasher()

    assert await hasher.hash(_PASSWORD) != await hasher.hash(_PASSWORD)


async def test_a_password_is_normalised_so_composed_and_decomposed_forms_match() -> None:
    hasher = PasswordHasher()
    composed = "caf\u00e9 au lait, s'il vous plait"
    decomposed = "cafe\u0301 au lait, s'il vous plait"

    encoded = await hasher.hash(composed)

    assert await hasher.verify(decomposed, encoded) is True


@pytest.mark.parametrize("length", [0, MIN_PASSWORD_CHARS - 1, MAX_PASSWORD_CHARS + 1])
async def test_hash_refuses_a_password_outside_the_length_bounds(length: int) -> None:
    candidate = "p" * length

    with pytest.raises(WeakPasswordError) as excinfo:
        await PasswordHasher().hash(candidate)

    assert str(MIN_PASSWORD_CHARS) in str(excinfo.value)
    assert "ppp" not in str(excinfo.value)  # The rule is stated; the password never is.


@pytest.mark.parametrize("length", [MIN_PASSWORD_CHARS, MAX_PASSWORD_CHARS])
def test_check_password_strength_accepts_both_bounds(length: int) -> None:
    check_password_strength("p" * length)


async def test_verify_refuses_an_over_long_password_or_a_malformed_hash_without_raising() -> None:
    hasher = PasswordHasher()
    encoded = await hasher.hash(_PASSWORD)

    assert await hasher.verify("p" * (MAX_PASSWORD_CHARS + 1), encoded) is False
    assert await hasher.verify(_PASSWORD, "not a phc string") is False
    assert await hasher.verify(_PASSWORD, encoded.replace("argon2id", "argon2i")) is False


async def test_derive_key_is_deterministic_per_salt_and_32_bytes_long() -> None:
    hasher = PasswordHasher()
    salt, other_salt = b"\x01" * SALT_BYTES, b"\x02" * SALT_BYTES

    first = await hasher.derive_key(_PASSWORD, salt)

    assert len(first) == 32
    assert await hasher.derive_key(_PASSWORD, salt) == first
    assert await hasher.derive_key(_PASSWORD, other_salt) != first


async def test_derive_key_refuses_a_salt_of_the_wrong_length() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        await PasswordHasher().derive_key(_PASSWORD, b"\x01" * (SALT_BYTES - 1))


def test_a_hasher_needs_at_least_one_slot() -> None:
    with pytest.raises(ValueError, match="at least one slot"):
        PasswordHasher(concurrency=0)


class _BlockingDerivation:
    """Stand in for the Argon2id call: count threads inside it and hold them until released."""

    def __init__(self, loop: asyncio.AbstractEventLoop, wanted: int) -> None:
        self._loop = loop
        self._wanted = wanted
        self._lock = threading.Lock()  # Guards the two counters across worker threads.
        self.in_flight = 0
        self.most_in_flight = 0
        self.entered = asyncio.Event()
        self.release = threading.Event()

    def __call__(self, password: str) -> str:
        """Record entry, wait for the test to release, record exit."""
        with self._lock:
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
            if self.in_flight == self._wanted:
                self._loop.call_soon_threadsafe(self.entered.set)
        self.release.wait(_RELEASE_TIMEOUT_S)
        with self._lock:
            self.in_flight -= 1
        return "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"


async def test_no_more_than_the_hashers_slots_derive_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The derivation itself is replaced by a blocking stand-in so the test can hold four
    # requests in flight and see how many actually entered the worker threads at once.
    spy = _BlockingDerivation(asyncio.get_running_loop(), wanted=2)
    monkeypatch.setattr(password_module, "_derive_phc", spy)
    hasher = PasswordHasher(concurrency=2)

    async with asyncio.TaskGroup() as group:
        for _ in range(4):
            group.create_task(hasher.hash(_PASSWORD))
        await asyncio.wait_for(spy.entered.wait(), _RELEASE_TIMEOUT_S)
        spy.release.set()

    assert spy.most_in_flight == 2


async def test_derivations_asked_for_at_once_by_two_hashers_all_finish() -> None:
    # OpenSSL draws each derivation's four lanes from one process-wide thread pool; two at once
    # on a machine with fewer than eight cores exhausted it, and both derivations hung for good.
    first, second = PasswordHasher(), PasswordHasher()
    encoded = await first.hash(_PASSWORD)

    async with asyncio.timeout(_CONCURRENT_TIMEOUT_S):
        matched = await asyncio.gather(
            *(hasher.verify(_PASSWORD, encoded) for hasher in (first, second, first, second))
        )

    assert matched == [True, True, True, True]
