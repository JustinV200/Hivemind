"""Contract suite for EntranceStore: one contract, run over both implementations.

Every allowed status edge is walked through the store and every forbidden one is refused by it,
so the state machine is proven where it is enforced, not only in its own table.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.entrance.store.protocol.EntranceStore contract and runs against
    hivemind.entrance.store.memory.MemoryEntranceStore and
    hivemind.entrance.store.sqlite.SqliteEntranceStore (a tmp_path SQLite file). A new
    implementation joins the fixture's params and passes here first (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.protocol for the protocol under test.
    - hivemind.entrance.enrol.state for the transition table.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from builders.entrance import (
    INVITE_TTL,
    PATHS,
    approve_changes,
    changes_for,
    make_device,
    make_invite,
    walk_to,
)
from pydantic import ValidationError

from hivemind.common.sqlite import connect
from hivemind.entrance.enrol import TRANSITIONS, DeviceStatus, EnrolledDevice
from hivemind.entrance.errors import (
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
    DeviceStatusConflictError,
    InvalidDeviceEntryError,
    InvalidDeviceTransitionError,
    InviteAlreadyExistsError,
    InviteAlreadyUsedError,
    InviteExpiredError,
    InviteNotFoundError,
)
from hivemind.entrance.store import EntranceStore, MemoryEntranceStore, SqliteEntranceStore
from waggle.clock import FakeClock
from waggle.ids import DeviceId

_STORE_KINDS = ("memory", "sqlite")
_PHC = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA"
)
_OTHER_PHC = _PHC.replace("aGFzaA", "b3RoZXI")
_ALLOWED = [(status, target) for status, targets in TRANSITIONS.items() for target in targets]
_FORBIDDEN = [
    pair for pair in itertools.product(DeviceStatus, DeviceStatus) if pair not in _ALLOWED
]
_MISSING = DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")


@pytest.fixture(params=_STORE_KINDS)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> EntranceStore:
    """An empty EntranceStore of the parametrised kind."""
    if request.param == "memory":
        return MemoryEntranceStore()
    return await SqliteEntranceStore.create(connect(tmp_path / "hive.sqlite3"), FakeClock())


async def _stored(store: EntranceStore, clock: FakeClock) -> EnrolledDevice:
    """Put a fresh INVITED device, the only way a device enters, and return it."""
    device = make_device(clock)
    await store.put_device(device)
    return device


# ──────────────────────────────────────────────────────────────────────────────
# The operator row
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_operator_row_is_absent_until_the_first_password(store: EntranceStore) -> None:
    assert await store.get_operator() is None


async def test_setting_the_password_again_keeps_created_at_and_moves_changed_at(
    store: EntranceStore,
) -> None:
    clock = FakeClock()
    first_at = clock.now()
    await store.set_operator_password_hash(_PHC, first_at)
    clock.advance(60)

    await store.set_operator_password_hash(_OTHER_PHC, clock.now())

    operator = await store.get_operator()
    assert operator is not None
    assert operator.password_hash == _OTHER_PHC
    assert operator.created_at == first_at
    assert operator.changed_at == clock.now()


async def test_the_operator_row_refuses_anything_but_an_argon2id_hash(store: EntranceStore) -> None:
    with pytest.raises(ValidationError):
        await store.set_operator_password_hash("correct horse battery staple", FakeClock().now())

    assert await store.get_operator() is None


# ──────────────────────────────────────────────────────────────────────────────
# Devices
# ──────────────────────────────────────────────────────────────────────────────


async def test_put_then_get_returns_an_equal_device(store: EntranceStore) -> None:
    device = make_device()

    await store.put_device(device)

    assert await store.get_device(device.id) == device


async def test_get_of_an_unknown_device_raises(store: EntranceStore) -> None:
    with pytest.raises(DeviceNotFoundError):
        await store.get_device(_MISSING)


async def test_put_refuses_a_duplicate_id(store: EntranceStore) -> None:
    device = make_device()
    await store.put_device(device)

    with pytest.raises(DeviceAlreadyExistsError):
        await store.put_device(device)


@pytest.mark.parametrize(
    ("status", "loopback_bound"),
    [
        (DeviceStatus.PENDING, False),
        (DeviceStatus.APPROVED, False),  # Only the console enters approved.
        (DeviceStatus.INVITED, True),  # The console never enters through an invite.
        (DeviceStatus.LOCKED, True),
        (DeviceStatus.REVOKED, False),
    ],
)
async def test_put_refuses_a_device_anywhere_but_the_entry(
    store: EntranceStore, status: DeviceStatus, loopback_bound: bool
) -> None:
    device = make_device(status=status, loopback_bound=loopback_bound, interactive=True)

    with pytest.raises(InvalidDeviceEntryError):
        await store.put_device(device)
    assert await store.list_devices() == ()


async def test_the_loopback_bound_console_enters_approved(store: EntranceStore) -> None:
    console = make_device(status=DeviceStatus.APPROVED, loopback_bound=True, interactive=True)

    await store.put_device(console)

    assert await store.get_device(console.id) == console


async def test_list_devices_filters_by_status_and_orders_oldest_first(store: EntranceStore) -> None:
    clock = FakeClock()
    first = make_device(clock)
    clock.advance(1)
    second = make_device(clock)
    await store.put_device(second)
    await store.put_device(first)
    approved = await walk_to(store, second, DeviceStatus.APPROVED, clock)

    assert await store.list_devices() == (first, approved)
    assert await store.list_devices(DeviceStatus.INVITED) == (first,)
    assert await store.list_devices(DeviceStatus.APPROVED) == (approved,)
    assert await store.list_devices(DeviceStatus.LOCKED) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Status changes: the state machine, enforced by the store
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("from_status", "to_status"), _ALLOWED)
async def test_every_allowed_edge_moves_the_stored_device(
    store: EntranceStore, from_status: DeviceStatus, to_status: DeviceStatus
) -> None:
    clock = FakeClock()
    device = await walk_to(store, await _stored(store, clock), from_status, clock)

    moved = await store.update_device_status(
        device.id, from_status, to_status, **changes_for(from_status, to_status, clock)
    )

    assert moved.status is to_status
    assert await store.get_device(device.id) == moved


@pytest.mark.parametrize(("from_status", "to_status"), _FORBIDDEN)
async def test_every_forbidden_edge_is_refused_and_changes_nothing(
    store: EntranceStore, from_status: DeviceStatus, to_status: DeviceStatus
) -> None:
    clock = FakeClock()
    device = await walk_to(store, await _stored(store, clock), from_status, clock)

    with pytest.raises(InvalidDeviceTransitionError):
        await store.update_device_status(
            device.id, from_status, to_status, **changes_for(from_status, to_status, clock)
        )

    assert await store.get_device(device.id) == device


async def test_a_stale_expected_status_is_refused(store: EntranceStore) -> None:
    clock = FakeClock()
    device = await walk_to(store, await _stored(store, clock), DeviceStatus.PENDING, clock)
    await store.update_device_status(device.id, DeviceStatus.PENDING, DeviceStatus.DENIED)

    # A second decision made from the same PENDING read (an approval racing the denial) loses.
    with pytest.raises(DeviceStatusConflictError):
        await store.update_device_status(
            device.id, DeviceStatus.PENDING, DeviceStatus.APPROVED, **approve_changes(clock)
        )

    assert (await store.get_device(device.id)).status is DeviceStatus.DENIED


async def test_a_status_change_of_an_unknown_device_raises(store: EntranceStore) -> None:
    with pytest.raises(DeviceNotFoundError):
        await store.update_device_status(_MISSING, DeviceStatus.INVITED, DeviceStatus.REVOKED)


async def test_approval_binds_what_the_operator_chose(store: EntranceStore) -> None:
    clock = FakeClock()
    pending = await walk_to(store, await _stored(store, clock), DeviceStatus.PENDING, clock)
    changes = approve_changes(clock)

    approved = await store.update_device_status(
        pending.id, DeviceStatus.PENDING, DeviceStatus.APPROVED, **changes
    )

    assert approved.capabilities == ("entrance:answer", "entrance:submit", "observe")
    assert approved.spend_cap_usd_per_day == changes["spend_cap_usd_per_day"]
    assert approved.approved_at == clock.now()
    assert (approved.id, approved.created_at, approved.public_key) == (
        pending.id,
        pending.created_at,
        pending.public_key,
    )


async def test_a_change_that_breaks_a_record_rule_is_refused_and_changes_nothing(
    store: EntranceStore,
) -> None:
    clock = FakeClock()
    pending = await walk_to(store, await _stored(store, clock), DeviceStatus.PENDING, clock)

    # An approval without approved_at would be an APPROVED record the model forbids.
    with pytest.raises(ValidationError):
        await store.update_device_status(pending.id, DeviceStatus.PENDING, DeviceStatus.APPROVED)

    assert await store.get_device(pending.id) == pending


async def test_a_status_change_cannot_smuggle_in_an_immutable_field(store: EntranceStore) -> None:
    device = await _stored(store, FakeClock())
    # Built at runtime, as a careless route might: the static type cannot see this key.
    smuggled: dict[str, Any] = {"loopback_bound": True}

    with pytest.raises(TypeError, match="loopback_bound"):
        await store.update_device_status(device.id, device.status, DeviceStatus.REVOKED, **smuggled)
    assert await store.get_device(device.id) == device


# ──────────────────────────────────────────────────────────────────────────────
# Invites
# ──────────────────────────────────────────────────────────────────────────────


async def test_put_then_get_invite_round_trips(store: EntranceStore) -> None:
    device = make_device()
    await store.put_device(device)
    invite = make_invite(device)

    await store.put_invite(invite)

    assert await store.get_invite(invite.code_hash) == invite


async def test_an_invite_needs_its_invited_device_to_exist_first(store: EntranceStore) -> None:
    clock = FakeClock()
    unknown = make_device(clock)
    approved = await walk_to(store, await _stored(store, clock), DeviceStatus.APPROVED, clock)

    with pytest.raises(DeviceNotFoundError):
        await store.put_invite(make_invite(unknown, clock))
    with pytest.raises(DeviceStatusConflictError):
        await store.put_invite(make_invite(approved, clock))


async def test_an_invite_code_and_a_device_are_each_invited_once(store: EntranceStore) -> None:
    clock = FakeClock()
    first, second = await _stored(store, clock), await _stored(store, clock)
    invite = make_invite(first, clock)
    await store.put_invite(invite)

    with pytest.raises(InviteAlreadyExistsError):
        await store.put_invite(make_invite(second, clock, code_hash=invite.code_hash))
    with pytest.raises(InviteAlreadyExistsError):
        await store.put_invite(make_invite(first, clock))


async def test_get_of_an_unknown_invite_raises(store: EntranceStore) -> None:
    with pytest.raises(InviteNotFoundError):
        await store.get_invite("f" * 64)
    with pytest.raises(InviteNotFoundError):
        await store.mark_invite_used("f" * 64, FakeClock().now())


async def test_an_invite_admits_once(store: EntranceStore) -> None:
    clock = FakeClock()
    invite = make_invite(await _stored(store, clock), clock)
    await store.put_invite(invite)
    clock.advance(60)

    used = await store.mark_invite_used(invite.code_hash, clock.now())

    assert used.used_at == clock.now()
    assert await store.get_invite(invite.code_hash) == used
    with pytest.raises(InviteAlreadyUsedError):
        await store.mark_invite_used(invite.code_hash, clock.now())


async def test_an_expired_invite_is_refused_and_stays_unused(store: EntranceStore) -> None:
    clock = FakeClock()
    invite = make_invite(await _stored(store, clock), clock)
    await store.put_invite(invite)

    with pytest.raises(InviteExpiredError):
        await store.mark_invite_used(invite.code_hash, clock.now() + INVITE_TTL)

    assert (await store.get_invite(invite.code_hash)).used_at is None
    assert await store.mark_invite_used(invite.code_hash, clock.now() + timedelta(minutes=1))


def test_the_builders_paths_reach_every_status() -> None:
    assert set(PATHS) == set(DeviceStatus)
