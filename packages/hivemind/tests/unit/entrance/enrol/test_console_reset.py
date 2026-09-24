"""Tests for hivemind.entrance.enrol.console's offline half: the reset, the unlock, the lookup.

``reset_operator`` (``hive entrance operator password --reset``) must leave no device admitted
but a new console, end every persisted session, and seal the new console key under the new
password only; ``unlock_console`` (``hive entrance unlock --console``) must move a locked console
back only for the right password; ``console_record`` must find the console for its key and nothing
else. Real Argon2id, in-memory tables and secret store, as the bootstrap's own tests use.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/console.py (codingrules section 3); split from
    test_console.py by feature (codingrules 5.1).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance import (
    edge_event,
    entry_event,
    make_device,
    make_identity,
    make_session,
    walk_to,
)

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.auth import PasswordHasher
from hivemind.entrance.enrol import (
    OPERATOR_RESET,
    ConsoleDeps,
    DeviceStatus,
    EnrolledDevice,
    bootstrap_operator,
    console_record,
    reset_operator,
    unlock_console,
    unlock_console_key,
)
from hivemind.entrance.errors import (
    DeviceStatusConflictError,
    KeyUnwrapError,
    OperatorNotInitialisedError,
    WeakPasswordError,
)
from hivemind.entrance.store import MemoryEntranceStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.signing import Ed25519Signer

_PASSWORD = "correct horse battery staple"  # noqa: S105 -- a test's password, not a credential
_NEW_PASSWORD = "a longer and entirely new password"  # noqa: S105 -- a test's password


@pytest.fixture
def trail() -> PheromoneTrail:
    """The trail the Hive Stand's Entrance tables record on."""
    return MemoryPheromoneTrail(FakeClock())


@pytest.fixture
def deps(trail: PheromoneTrail) -> ConsoleDeps:
    """A fresh Hive Stand: empty Entrance tables and secret store, one real hasher."""
    clock = FakeClock()
    return ConsoleDeps(
        store=MemoryEntranceStore(trail),
        secrets=MemorySecretStore(),
        hasher=PasswordHasher(),
        clock=clock,
        identity=make_identity(clock, actor="human"),
    )


async def _device(deps: ConsoleDeps, status: DeviceStatus) -> EnrolledDevice:
    """Record a device and walk it to ``status`` through the tables' own state machine."""
    device = make_device(deps.clock)
    await deps.store.put_device(device, entry_event(device, deps.clock))
    return await walk_to(deps.store, device, status, deps.clock)


async def _lock(deps: ConsoleDeps, device: EnrolledDevice) -> None:
    """Lock ``device`` as a lockout would."""
    event = edge_event(device, DeviceStatus.APPROVED, DeviceStatus.LOCKED, deps.clock)
    await deps.store.update_device_status(
        device.id, DeviceStatus.APPROVED, DeviceStatus.LOCKED, event
    )


async def test_a_reset_dismisses_every_device_and_ends_every_session(deps: ConsoleDeps) -> None:
    old_console = await bootstrap_operator(deps, _PASSWORD)
    statuses = (
        DeviceStatus.INVITED,
        DeviceStatus.PENDING,
        DeviceStatus.APPROVED,
        DeviceStatus.LOCKED,
    )
    devices = [await _device(deps, status) for status in statuses]
    await deps.store.sessions.put(make_session(devices[2].id, deps.clock))

    console = await reset_operator(deps, _NEW_PASSWORD)

    after = {device.id: device.status for device in await deps.store.list_devices()}
    assert [after[device.id] for device in devices] == [
        DeviceStatus.REVOKED,
        DeviceStatus.DENIED,
        DeviceStatus.REVOKED,
        DeviceStatus.REVOKED,
    ]
    assert after[old_console.id] is DeviceStatus.REVOKED
    assert console.id != old_console.id and console.status is DeviceStatus.APPROVED
    assert console.loopback_bound
    assert await deps.store.sessions.list_open() == ()


async def test_after_a_reset_only_the_new_password_opens_the_new_console(
    deps: ConsoleDeps,
) -> None:
    await bootstrap_operator(deps, _PASSWORD)

    console = await reset_operator(deps, _NEW_PASSWORD)

    signer = await unlock_console_key(deps.secrets, deps.hasher, _NEW_PASSWORD)
    assert (await console_record(deps.store, signer)).id == console.id
    with pytest.raises(KeyUnwrapError):
        await unlock_console_key(deps.secrets, deps.hasher, _PASSWORD)
    operator = await deps.store.get_operator()
    assert operator is not None
    assert await deps.hasher.verify(_NEW_PASSWORD, operator.password_hash)


async def test_a_reset_records_each_dismissal_with_its_reason(
    deps: ConsoleDeps, trail: PheromoneTrail
) -> None:
    await bootstrap_operator(deps, _PASSWORD)
    pending = await _device(deps, DeviceStatus.PENDING)

    await reset_operator(deps, _NEW_PASSWORD)

    denied = await trail.query(TrailQuery(kind="guard.entrance_denied"))
    revoked = await trail.query(TrailQuery(kind="guard.entrance_revoked"))
    assert [(event.subject_id, event.payload["reason"]) for event in denied] == [
        (pending.id, OPERATOR_RESET)
    ]
    assert all(event.payload["reason"] == OPERATOR_RESET for event in revoked)
    assert all(_NEW_PASSWORD not in event.model_dump_json() for event in (*denied, *revoked))


async def test_a_reset_run_twice_leaves_exactly_one_live_console(deps: ConsoleDeps) -> None:
    await bootstrap_operator(deps, _PASSWORD)

    await reset_operator(deps, _NEW_PASSWORD)
    second = await reset_operator(deps, _NEW_PASSWORD)

    live = [
        device
        for device in await deps.store.list_devices()
        if device.loopback_bound and device.status is DeviceStatus.APPROVED
    ]
    assert [device.id for device in live] == [second.id]


async def test_a_reset_before_any_operator_is_refused(deps: ConsoleDeps) -> None:
    with pytest.raises(OperatorNotInitialisedError):
        await reset_operator(deps, _NEW_PASSWORD)


async def test_a_reset_to_a_weak_password_moves_nothing(deps: ConsoleDeps) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)

    with pytest.raises(WeakPasswordError):
        await reset_operator(deps, "short")

    assert (await deps.store.get_device(console.id)).status is DeviceStatus.APPROVED


async def test_unlock_console_returns_a_locked_console_to_approved(deps: ConsoleDeps) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)
    await _lock(deps, console)

    unlocked = await unlock_console(deps, _PASSWORD)

    assert unlocked.id == console.id and unlocked.status is DeviceStatus.APPROVED


async def test_unlock_console_with_the_wrong_password_leaves_it_locked(deps: ConsoleDeps) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)
    await _lock(deps, console)

    with pytest.raises(KeyUnwrapError):
        await unlock_console(deps, _NEW_PASSWORD)

    assert (await deps.store.get_device(console.id)).status is DeviceStatus.LOCKED


async def test_unlock_console_when_it_is_not_locked_is_a_conflict(deps: ConsoleDeps) -> None:
    await bootstrap_operator(deps, _PASSWORD)

    with pytest.raises(DeviceStatusConflictError):
        await unlock_console(deps, _PASSWORD)


async def test_console_record_knows_the_console_by_its_key_and_no_stranger(
    deps: ConsoleDeps,
) -> None:
    console = await bootstrap_operator(deps, _PASSWORD)
    signer = await unlock_console_key(deps.secrets, deps.hasher, _PASSWORD)

    found = await console_record(deps.store, signer)

    assert found.id == console.id
    with pytest.raises(OperatorNotInitialisedError):
        await console_record(deps.store, Ed25519Signer.generate())
