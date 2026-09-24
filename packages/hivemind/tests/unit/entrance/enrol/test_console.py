"""Tests for hivemind.entrance.enrol.console: the operator bootstrap, password change and unlock.

Real Argon2id throughout (about a tenth of a second per derivation), over in-memory Entrance
tables and an in-memory secret store, so each test sets up a fresh Hive Stand in milliseconds of
I/O and a few derivations.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/console.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.console for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.auth import KeyKind, PasswordHasher, b64url_encode, wrap_private_key
from hivemind.entrance.enrol import (
    CONSOLE_CAPABILITIES,
    CONSOLE_KEY_NAME,
    ConsoleDeps,
    DeviceStatus,
    bootstrap_operator,
    change_operator_password,
    unlock_console_key,
)
from hivemind.entrance.errors import (
    KeyUnwrapError,
    OperatorAlreadyInitialisedError,
    OperatorNotInitialisedError,
    OperatorPasswordMismatchError,
    WeakPasswordError,
)
from hivemind.entrance.store import MemoryEntranceStore
from waggle.clock import FakeClock

_PASSWORD = "correct horse battery staple"  # noqa: S105 -- a test's password, not a credential
_NEW_PASSWORD = "a longer and entirely new password"  # noqa: S105 -- a test's password, not a credential


@pytest.fixture
def deps() -> ConsoleDeps:
    """A fresh Hive Stand: empty Entrance tables, an empty secret store, one hasher."""
    return ConsoleDeps(
        store=MemoryEntranceStore(),
        secrets=MemorySecretStore(),
        hasher=PasswordHasher(),
        clock=FakeClock(),
    )


async def test_bootstrap_records_the_approved_loopback_bound_console(deps: ConsoleDeps) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)

    assert await deps.store.list_devices() == (console,)
    assert console.status is DeviceStatus.APPROVED
    assert console.loopback_bound is True
    assert console.interactive is True
    assert console.key_kind is KeyKind.ED25519
    assert console.capabilities == tuple(sorted(CONSOLE_CAPABILITIES))
    assert console.spend_cap_usd_per_day is None
    assert console.expires_at is None
    assert console.approved_at == console.created_at == deps.clock.now()


async def test_bootstrap_sets_the_password_hash_last_and_it_verifies(deps: ConsoleDeps) -> None:
    await bootstrap_operator(deps, _PASSWORD)

    operator = await deps.store.get_operator()

    assert operator is not None
    assert await deps.hasher.verify(_PASSWORD, operator.password_hash) is True


async def test_the_console_key_is_stored_wrapped_and_opens_only_with_the_password(
    deps: ConsoleDeps,
) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)

    signer = await unlock_console_key(deps.secrets, deps.hasher, _PASSWORD)
    blob = await deps.secrets.get(CONSOLE_KEY_NAME)

    assert b64url_encode(signer.public_key_bytes) == console.public_key
    assert blob is not None
    assert signer.private_key_bytes not in blob
    with pytest.raises(KeyUnwrapError):
        await unlock_console_key(deps.secrets, deps.hasher, "the wrong password entirely")


async def test_a_second_bootstrap_raises_and_writes_nothing(deps: ConsoleDeps) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)
    blob = await deps.secrets.get(CONSOLE_KEY_NAME)

    with pytest.raises(OperatorAlreadyInitialisedError):
        await bootstrap_operator(deps, _NEW_PASSWORD)

    assert await deps.store.list_devices() == (console,)
    assert await deps.secrets.get(CONSOLE_KEY_NAME) == blob


async def test_a_weak_password_is_refused_before_anything_is_written(deps: ConsoleDeps) -> None:
    with pytest.raises(WeakPasswordError):
        await bootstrap_operator(deps, "too short")

    assert await deps.store.get_operator() is None
    assert await deps.store.list_devices() == ()
    assert await deps.secrets.names() == ()


async def test_an_interrupted_bootstrap_reuses_the_key_and_record_it_left(
    deps: ConsoleDeps,
) -> None:
    # An earlier run wrapped a key and recorded the console, then died before the password row.
    first = await bootstrap_operator(deps, _PASSWORD)
    fresh = ConsoleDeps(MemoryEntranceStore(), deps.secrets, deps.hasher, deps.clock)
    await fresh.store.put_device(first)

    again = await bootstrap_operator(fresh, _PASSWORD)

    assert again == first
    assert await fresh.store.list_devices() == (first,)


async def test_a_bootstrap_retried_with_another_password_replaces_the_key_it_cannot_open(
    deps: ConsoleDeps,
) -> None:
    # The interrupted run used another password: its key cannot be opened, so it is replaced,
    # and the console record holding the old key is revoked rather than left live.
    stale = await bootstrap_operator(deps, _PASSWORD)
    fresh = ConsoleDeps(MemoryEntranceStore(), deps.secrets, deps.hasher, deps.clock)
    await fresh.store.put_device(stale)

    console = await bootstrap_operator(fresh, _NEW_PASSWORD)

    assert console.public_key != stale.public_key
    assert (await fresh.store.get_device(stale.id)).status is DeviceStatus.REVOKED
    assert await fresh.store.list_devices(DeviceStatus.APPROVED) == (console,)
    await unlock_console_key(fresh.secrets, fresh.hasher, _NEW_PASSWORD)


async def test_unlock_before_any_bootstrap_says_there_is_no_console(deps: ConsoleDeps) -> None:
    with pytest.raises(OperatorNotInitialisedError, match=CONSOLE_KEY_NAME):
        await unlock_console_key(deps.secrets, deps.hasher, _PASSWORD)


async def test_changing_the_password_rewraps_the_same_console_key(deps: ConsoleDeps) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)

    await change_operator_password(deps, _PASSWORD, _NEW_PASSWORD)

    operator = await deps.store.get_operator()
    assert operator is not None
    assert await deps.hasher.verify(_NEW_PASSWORD, operator.password_hash) is True
    signer = await unlock_console_key(deps.secrets, deps.hasher, _NEW_PASSWORD)
    assert b64url_encode(signer.public_key_bytes) == console.public_key
    with pytest.raises(KeyUnwrapError):
        await unlock_console_key(deps.secrets, deps.hasher, _PASSWORD)


async def test_a_change_with_the_wrong_current_password_changes_nothing(deps: ConsoleDeps) -> None:
    await bootstrap_operator(deps, _PASSWORD)
    before = await deps.store.get_operator()

    with pytest.raises(OperatorPasswordMismatchError):
        await change_operator_password(deps, "not the current password", _NEW_PASSWORD)

    assert await deps.store.get_operator() == before


async def test_a_change_to_a_weak_password_changes_nothing(deps: ConsoleDeps) -> None:
    await bootstrap_operator(deps, _PASSWORD)
    blob = await deps.secrets.get(CONSOLE_KEY_NAME)

    with pytest.raises(WeakPasswordError):
        await change_operator_password(deps, _PASSWORD, "short")

    assert await deps.secrets.get(CONSOLE_KEY_NAME) == blob


async def test_a_change_before_bootstrap_or_without_the_console_key_is_refused(
    deps: ConsoleDeps,
) -> None:
    with pytest.raises(OperatorNotInitialisedError):
        await change_operator_password(deps, _PASSWORD, _NEW_PASSWORD)
    await bootstrap_operator(deps, _PASSWORD)
    await deps.secrets.delete(CONSOLE_KEY_NAME)

    with pytest.raises(OperatorNotInitialisedError, match=CONSOLE_KEY_NAME):
        await change_operator_password(deps, _PASSWORD, _NEW_PASSWORD)


async def test_rerunning_a_change_that_died_after_the_rewrap_finishes_it(deps: ConsoleDeps) -> None:
    # The earlier run re-wrapped the key under the new password, then died before the hash.
    await bootstrap_operator(deps, _PASSWORD)
    signer = await unlock_console_key(deps.secrets, deps.hasher, _PASSWORD)
    rewrapped = await wrap_private_key(
        deps.hasher, _NEW_PASSWORD, CONSOLE_KEY_NAME, signer.private_key_bytes
    )
    await deps.secrets.put(CONSOLE_KEY_NAME, rewrapped)

    await change_operator_password(deps, _PASSWORD, _NEW_PASSWORD)

    operator = await deps.store.get_operator()
    assert operator is not None
    assert await deps.hasher.verify(_NEW_PASSWORD, operator.password_hash) is True
    unlocked = await unlock_console_key(deps.secrets, deps.hasher, _NEW_PASSWORD)
    assert unlocked.public_key_bytes == signer.public_key_bytes


async def test_a_console_key_that_opens_with_neither_password_is_reported_damaged(
    deps: ConsoleDeps,
) -> None:
    await bootstrap_operator(deps, _PASSWORD)
    foreign = await wrap_private_key(
        deps.hasher, "a third password nobody knows", CONSOLE_KEY_NAME, bytes(32)
    )
    await deps.secrets.put(CONSOLE_KEY_NAME, foreign)

    with pytest.raises(KeyUnwrapError):
        await change_operator_password(deps, _PASSWORD, _NEW_PASSWORD)


def test_the_console_capabilities_are_sorted_and_hold_no_duplicates() -> None:
    assert list(CONSOLE_CAPABILITIES) == sorted(set(CONSOLE_CAPABILITIES))
