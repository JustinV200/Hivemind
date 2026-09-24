"""Contract suite for EntranceStore: one contract, run over both implementations.

Every allowed status edge is walked through the store and every forbidden one is refused by it,
so the state machine is proven where it is enforced, not only in its own table. Every entry and
every change carries its ``guard.entrance_*`` event: the suite checks the event lands on the trail
with the change, and that a refused change leaves neither the record nor the trail touched.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.entrance.store.protocol.EntranceStore contract and runs against
    hivemind.entrance.store.memory.MemoryEntranceStore and
    hivemind.entrance.store.sqlite.SqliteEntranceStore (a tmp_path SQLite file shared with a
    SqlitePheromoneTrail). A new implementation joins the fixture's params and passes here first
    (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.protocol for the protocol under test.
    - hivemind.entrance.enrol.state for the transition table.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from builders.entrance import (
    INVITE_TTL,
    PATHS,
    approve_changes,
    changes_for,
    edge_event,
    entry_event,
    make_device,
    make_identity,
    make_invite,
    redeem_changes,
    walk_to,
)
from pydantic import ValidationError

from hivemind.common.errors import InvariantViolationError
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
from hivemind.pheromone import (
    DuplicateEventError,
    GuardEvent,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
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
_S = DeviceStatus


@dataclass(frozen=True, slots=True)
class _Tables:
    """An EntranceStore and the trail it records its events on, of one parametrised kind."""

    store: EntranceStore
    trail: PheromoneTrail


@pytest.fixture(params=_STORE_KINDS)
async def tables(request: pytest.FixtureRequest, tmp_path: Path) -> _Tables:
    """Empty Entrance tables of the parametrised kind, over a trail they share one file with."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _Tables(MemoryEntranceStore(memory_trail), memory_trail)
    # SQLite: the trail's table first (the store refuses a file without it), each its own
    # connection to the same file, as two stores writing one file always are (ADR-0006).
    db = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db), clock)
    return _Tables(await SqliteEntranceStore.create(connect(db), clock), sqlite_trail)


@pytest.fixture
def store(tables: _Tables) -> EntranceStore:
    """The store under test."""
    return tables.store


@pytest.fixture
def trail(tables: _Tables) -> PheromoneTrail:
    """The trail the store under test records on."""
    return tables.trail


async def _stored(store: EntranceStore, clock: FakeClock) -> EnrolledDevice:
    """Put a fresh INVITED device, the only way a device enters, and return it."""
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))
    return device


async def _kinds(trail: PheromoneTrail, subject: str) -> list[str]:
    """Return the kinds recorded about ``subject``, in trail order."""
    return [event.kind for event in await trail.query(TrailQuery(subject_id=subject))]


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
# Devices and their entry events
# ──────────────────────────────────────────────────────────────────────────────


async def test_put_stores_the_device_and_records_its_entry_event(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    device = make_device()
    event = entry_event(device)

    await store.put_device(device, event)

    assert await store.get_device(device.id) == device
    assert await trail.query(TrailQuery(subject_id=device.id)) == (event,)


async def test_get_of_an_unknown_device_raises(store: EntranceStore) -> None:
    with pytest.raises(DeviceNotFoundError):
        await store.get_device(_MISSING)


async def test_put_refuses_a_duplicate_id_and_records_nothing_more(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    device = make_device()
    await store.put_device(device, entry_event(device))

    with pytest.raises(DeviceAlreadyExistsError):
        await store.put_device(device, entry_event(device))
    assert await _kinds(trail, device.id) == ["guard.entrance_invited"]


@pytest.mark.parametrize(
    ("status", "loopback_bound"),
    [
        (_S.PENDING, False),
        (_S.APPROVED, False),  # Only the console enters approved.
        (_S.INVITED, True),  # The console never enters through an invite.
        (_S.LOCKED, True),
        (_S.REVOKED, False),
    ],
)
async def test_put_refuses_a_device_anywhere_but_the_entry(
    store: EntranceStore, trail: PheromoneTrail, status: DeviceStatus, loopback_bound: bool
) -> None:
    device = make_device(status=status, loopback_bound=loopback_bound, interactive=True)

    with pytest.raises(InvalidDeviceEntryError):
        await store.put_device(device, entry_event(device))
    assert await store.list_devices() == ()
    assert await trail.query(TrailQuery()) == ()


async def test_the_loopback_bound_console_enters_approved_as_an_approval(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    console = make_device(status=_S.APPROVED, loopback_bound=True, interactive=True)

    await store.put_device(console, entry_event(console))

    assert await store.get_device(console.id) == console
    assert await _kinds(trail, console.id) == ["guard.entrance_approved"]


async def test_put_refuses_an_event_that_is_not_the_entrys_own(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    clock = FakeClock()
    device, other = make_device(clock), make_device(clock)
    wrong_kind = make_identity(clock).event(clock, "guard.entrance_approved", device.id, {})

    with pytest.raises(InvariantViolationError):
        await store.put_device(device, wrong_kind)
    with pytest.raises(InvariantViolationError):
        await store.put_device(device, entry_event(other, clock))
    assert await store.list_devices() == ()
    assert await trail.query(TrailQuery()) == ()


async def test_list_devices_filters_by_status_and_orders_oldest_first(store: EntranceStore) -> None:
    clock = FakeClock()
    first = make_device(clock)
    clock.advance(1)
    second = make_device(clock)
    await store.put_device(second, entry_event(second, clock))
    await store.put_device(first, entry_event(first, clock))
    approved = await walk_to(store, second, _S.APPROVED, clock)

    assert await store.list_devices() == (first, approved)
    assert await store.list_devices(_S.INVITED) == (first,)
    assert await store.list_devices(_S.APPROVED) == (approved,)
    assert await store.list_devices(_S.LOCKED) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Status changes: the state machine, enforced by the store, each with its event
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("from_status", "to_status"), _ALLOWED)
async def test_every_allowed_edge_moves_the_device_and_records_its_event(
    store: EntranceStore, trail: PheromoneTrail, from_status: DeviceStatus, to_status: DeviceStatus
) -> None:
    clock = FakeClock()
    device = await walk_to(store, await _stored(store, clock), from_status, clock)
    event = edge_event(device, from_status, to_status, clock)

    moved = await store.update_device_status(
        device.id, from_status, to_status, event, **changes_for(from_status, to_status, clock)
    )

    assert moved.status is to_status
    assert await store.get_device(device.id) == moved
    assert (await trail.query(TrailQuery(subject_id=device.id)))[-1] == event


@pytest.mark.parametrize(("from_status", "to_status"), _FORBIDDEN)
async def test_every_forbidden_edge_is_refused_and_changes_nothing(
    store: EntranceStore, trail: PheromoneTrail, from_status: DeviceStatus, to_status: DeviceStatus
) -> None:
    clock = FakeClock()
    device = await walk_to(store, await _stored(store, clock), from_status, clock)
    recorded = await trail.query(TrailQuery())

    with pytest.raises(InvalidDeviceTransitionError):
        await store.update_device_status(
            device.id,
            from_status,
            to_status,
            edge_event(device, from_status, to_status, clock),
            **changes_for(from_status, to_status, clock),
        )

    assert await store.get_device(device.id) == device
    assert await trail.query(TrailQuery()) == recorded


async def test_a_stale_expected_status_is_refused_and_records_nothing(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    clock = FakeClock()
    device = await walk_to(store, await _stored(store, clock), _S.PENDING, clock)
    denial = edge_event(device, _S.PENDING, _S.DENIED, clock)
    await store.update_device_status(device.id, _S.PENDING, _S.DENIED, denial)

    # A second decision made from the same PENDING read (an approval racing the denial) loses.
    with pytest.raises(DeviceStatusConflictError):
        await store.update_device_status(
            device.id,
            _S.PENDING,
            _S.APPROVED,
            edge_event(device, _S.PENDING, _S.APPROVED, clock),
            **approve_changes(clock),
        )

    assert (await store.get_device(device.id)).status is _S.DENIED
    assert (await _kinds(trail, device.id))[-1] == "guard.entrance_denied"


async def test_a_status_change_of_an_unknown_device_raises(store: EntranceStore) -> None:
    ghost = make_device()

    with pytest.raises(DeviceNotFoundError):
        await store.update_device_status(
            ghost.id, _S.INVITED, _S.REVOKED, edge_event(ghost, _S.INVITED, _S.REVOKED, FakeClock())
        )


async def test_a_change_with_another_edges_or_devices_event_is_refused(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    clock = FakeClock()
    device, other = await _stored(store, clock), await _stored(store, clock)
    recorded = await trail.query(TrailQuery())

    with pytest.raises(InvariantViolationError):
        await store.update_device_status(
            device.id, _S.INVITED, _S.REVOKED, edge_event(device, _S.INVITED, _S.EXPIRED, clock)
        )
    with pytest.raises(InvariantViolationError):
        await store.update_device_status(
            device.id, _S.INVITED, _S.REVOKED, edge_event(other, _S.INVITED, _S.REVOKED, clock)
        )
    assert await store.get_device(device.id) == device
    assert await trail.query(TrailQuery()) == recorded


async def test_a_change_whose_event_cannot_be_recorded_is_not_applied(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    clock = FakeClock()
    device = await _stored(store, clock)
    event = edge_event(device, _S.INVITED, _S.REVOKED, clock)
    # The same event id already on the trail: the change and its event must fail together.
    await trail.record(event)

    with pytest.raises(DuplicateEventError):
        await store.update_device_status(device.id, _S.INVITED, _S.REVOKED, event)
    assert await store.get_device(device.id) == device


async def test_approval_binds_what_the_operator_chose(store: EntranceStore) -> None:
    clock = FakeClock()
    pending = await walk_to(store, await _stored(store, clock), _S.PENDING, clock)
    changes = approve_changes(clock)
    event = edge_event(pending, _S.PENDING, _S.APPROVED, clock)

    approved = await store.update_device_status(
        pending.id, _S.PENDING, _S.APPROVED, event, **changes
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
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    clock = FakeClock()
    pending = await walk_to(store, await _stored(store, clock), _S.PENDING, clock)
    recorded = await trail.query(TrailQuery())

    # An approval without approved_at would be an APPROVED record the model forbids.
    with pytest.raises(ValidationError):
        await store.update_device_status(
            pending.id, _S.PENDING, _S.APPROVED, edge_event(pending, _S.PENDING, _S.APPROVED, clock)
        )

    assert await store.get_device(pending.id) == pending
    assert await trail.query(TrailQuery()) == recorded


async def test_a_status_change_cannot_smuggle_in_an_immutable_field(store: EntranceStore) -> None:
    clock = FakeClock()
    device = await _stored(store, clock)
    # Built at runtime, as a careless route might: the static type cannot see this key.
    smuggled: dict[str, Any] = {"loopback_bound": True}
    event = edge_event(device, _S.INVITED, _S.REVOKED, clock)

    with pytest.raises(TypeError, match="loopback_bound"):
        await store.update_device_status(device.id, _S.INVITED, _S.REVOKED, event, **smuggled)
    assert await store.get_device(device.id) == device


# ──────────────────────────────────────────────────────────────────────────────
# Invites, and redemption in one step
# ──────────────────────────────────────────────────────────────────────────────


def _pending_event(device: EnrolledDevice, clock: FakeClock) -> GuardEvent:
    """The event a redemption of ``device``'s invite records."""
    return edge_event(device, _S.INVITED, _S.PENDING, clock)


async def test_put_then_get_invite_round_trips(store: EntranceStore) -> None:
    device = await _stored(store, FakeClock())
    invite = make_invite(device)

    await store.put_invite(invite)

    assert await store.get_invite(invite.code_hash) == invite


async def test_an_invite_needs_its_invited_device_to_exist_first(store: EntranceStore) -> None:
    clock = FakeClock()
    unknown = make_device(clock)
    approved = await walk_to(store, await _stored(store, clock), _S.APPROVED, clock)

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


async def test_get_or_redeem_of_an_unknown_invite_raises(store: EntranceStore) -> None:
    clock = FakeClock()

    with pytest.raises(InviteNotFoundError):
        await store.get_invite("f" * 64)
    with pytest.raises(InviteNotFoundError):
        await store.redeem_invite(
            "f" * 64, clock.now(), _pending_event(make_device(clock), clock), **redeem_changes()
        )


async def test_redeeming_spends_the_invite_and_moves_its_device_with_its_event(
    store: EntranceStore, trail: PheromoneTrail
) -> None:
    clock = FakeClock()
    device = await _stored(store, clock)
    invite = make_invite(device, clock)
    await store.put_invite(invite)
    clock.advance(60)
    event = _pending_event(device, clock)

    pending = await store.redeem_invite(
        invite.code_hash, clock.now(), event, **redeem_changes(clock)
    )

    assert pending.status is _S.PENDING
    assert await store.get_device(device.id) == pending
    assert (await store.get_invite(invite.code_hash)).used_at == clock.now()
    assert (await trail.query(TrailQuery(subject_id=device.id)))[-1] == event


async def test_an_invite_admits_once(store: EntranceStore, trail: PheromoneTrail) -> None:
    clock = FakeClock()
    device = await _stored(store, clock)
    invite = make_invite(device, clock)
    await store.put_invite(invite)
    await store.redeem_invite(
        invite.code_hash, clock.now(), _pending_event(device, clock), **redeem_changes(clock)
    )
    recorded = await trail.query(TrailQuery())

    with pytest.raises(InviteAlreadyUsedError):
        await store.redeem_invite(
            invite.code_hash, clock.now(), _pending_event(device, clock), **redeem_changes(clock)
        )
    assert await trail.query(TrailQuery()) == recorded


async def test_an_expired_invite_is_refused_and_stays_unused(store: EntranceStore) -> None:
    clock = FakeClock()
    device = await _stored(store, clock)
    invite = make_invite(device, clock)
    await store.put_invite(invite)
    late, event = clock.now() + INVITE_TTL, _pending_event(device, clock)

    with pytest.raises(InviteExpiredError):
        await store.redeem_invite(invite.code_hash, late, event, **redeem_changes(clock))

    assert (await store.get_invite(invite.code_hash)).used_at is None
    assert (await store.get_device(device.id)).status is _S.INVITED
    just_in_time = clock.now() + timedelta(minutes=1)
    assert await store.redeem_invite(invite.code_hash, just_in_time, event, **redeem_changes())


async def test_an_invite_whose_device_left_invited_is_refused_and_stays_unused(
    store: EntranceStore,
) -> None:
    clock = FakeClock()
    device = await _stored(store, clock)
    invite = make_invite(device, clock)
    await store.put_invite(invite)
    await walk_to(store, device, _S.REVOKED, clock)

    with pytest.raises(DeviceStatusConflictError):
        await store.redeem_invite(
            invite.code_hash, clock.now(), _pending_event(device, clock), **redeem_changes(clock)
        )
    assert (await store.get_invite(invite.code_hash)).used_at is None


async def test_a_redemption_with_another_devices_event_is_refused(store: EntranceStore) -> None:
    clock = FakeClock()
    device, other = await _stored(store, clock), await _stored(store, clock)
    invite = make_invite(device, clock)
    await store.put_invite(invite)

    with pytest.raises(InvariantViolationError):
        await store.redeem_invite(
            invite.code_hash, clock.now(), _pending_event(other, clock), **redeem_changes(clock)
        )
    assert (await store.get_invite(invite.code_hash)).used_at is None


def test_the_builders_paths_reach_every_status() -> None:
    assert set(PATHS) == set(DeviceStatus)
