"""Contract suite for the 10.5e Entrance tables: logins, pending confirmations, the mode, logins.

Run over both EntranceStore implementations: ``record_login`` (a login recorded on an APPROVED
device, its passkey counter only ever moving forward), the ``LoginTable`` (consecutive failures,
known networks), the ``PendingTable`` (put PENDING, settled once along an edge from the expected
status) and the ``ModeTable`` (OPEN until changed, each change written with its event, from the
expected mode). Sessions have their own suite (test_entrance_session_table_contract.py).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.entrance.store protocols and runs against MemoryEntranceStore and
    SqliteEntranceStore (a tmp_path SQLite file shared with a SqlitePheromoneTrail), codingrules
    14.3.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.protocol, .logins, .pending and .mode for the protocols under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from builders.entrance import (
    STORE_IDENTITY,
    approve_changes,
    entry_event,
    make_description,
    make_device,
    make_pending,
    pending_event,
    walk_to,
)
from pydantic import ValidationError

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.entrance.auth import KeyKind, PendingStatus, b64url_encode
from hivemind.entrance.auth.confirm import Settlement
from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice, trail_kind
from hivemind.entrance.errors import (
    DeviceNotFoundError,
    DeviceStatusConflictError,
    EntranceModeConflictError,
    InvalidModeTransitionError,
    InvalidPendingTransitionError,
    PasskeyRejectedError,
    PendingNotFoundError,
    PendingStatusConflictError,
)
from hivemind.entrance.reducer import EntranceMode
from hivemind.entrance.store import EntranceStore, MemoryEntranceStore, SqliteEntranceStore
from hivemind.pheromone import (
    GuardEvent,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
from waggle.clock import FakeClock
from waggle.ids import DeviceId

_MISSING = DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")
_S = DeviceStatus
_P = PendingStatus


@dataclass(frozen=True, slots=True)
class _Rig:
    """An EntranceStore, the trail it records on, an APPROVED device, and the clock."""

    store: EntranceStore
    trail: PheromoneTrail
    device: EnrolledDevice
    clock: FakeClock


@pytest.fixture(params=["memory", "sqlite"])
async def rig(request: pytest.FixtureRequest, tmp_path: Path) -> _Rig:
    """Tables of the parametrised kind holding one APPROVED Ed25519 device."""
    clock = FakeClock()
    store: EntranceStore
    trail: PheromoneTrail
    if request.param == "memory":
        trail = MemoryPheromoneTrail(clock)
        store = MemoryEntranceStore(trail)
    else:
        db = tmp_path / "hive.sqlite3"
        trail = await SqlitePheromoneTrail.create(connect(db), clock)
        store = await SqliteEntranceStore.create(connect(db), clock)
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))
    approved = await walk_to(store, device, _S.APPROVED, clock)
    return _Rig(store, trail, approved, clock)


async def _passkey_device(rig: _Rig) -> EnrolledDevice:
    """Store an APPROVED passkey device whose counter stands at 3."""
    device = make_device(rig.clock)
    await rig.store.put_device(device, entry_event(device, rig.clock))
    pending = STORE_IDENTITY.event(rig.clock, trail_kind(_S.INVITED, _S.PENDING), device.id, {})
    await rig.store.update_device_status(
        device.id,
        _S.INVITED,
        _S.PENDING,
        pending,
        key_kind=KeyKind.PASSKEY,
        public_key=b64url_encode(b"a COSE key"),
        credential_id=b64url_encode(b"credential"),
        sign_count=3,
        rp_id="localhost",
        interactive=True,
        description=make_description(),
        expires_at=rig.clock.now() + timedelta(hours=1),
    )
    approved = STORE_IDENTITY.event(rig.clock, trail_kind(_S.PENDING, _S.APPROVED), device.id, {})
    changes = approve_changes(rig.clock)
    return await rig.store.update_device_status(
        device.id, _S.PENDING, _S.APPROVED, approved, **changes
    )


def _mode_event(rig: _Rig, kind: str) -> GuardEvent:
    """Build a mode change's event about the Hive."""
    return STORE_IDENTITY.event(rig.clock, kind, STORE_IDENTITY.hive_id, {"reason": "operator"})


# ──────────────────────────────────────────────────────────────────────────────
# record_login
# ──────────────────────────────────────────────────────────────────────────────


async def test_record_login_marks_the_device_seen_and_keeps_its_status(rig: _Rig) -> None:
    at = rig.clock.now() + timedelta(minutes=1)

    updated = await rig.store.record_login(rig.device.id, at, "100.64.3.0/24", None)

    assert (updated.status, updated.last_seen_at, updated.last_network) == (
        _S.APPROVED,
        at,
        "100.64.3.0/24",
    )
    assert await rig.store.get_device(rig.device.id) == updated


async def test_record_login_refuses_a_device_that_is_not_approved(rig: _Rig) -> None:
    device = make_device(rig.clock)
    await rig.store.put_device(device, entry_event(device, rig.clock))
    locked = await walk_to(rig.store, device, _S.LOCKED, rig.clock)

    with pytest.raises(DeviceStatusConflictError):
        await rig.store.record_login(locked.id, rig.clock.now(), None, None)
    with pytest.raises(DeviceNotFoundError):
        await rig.store.record_login(_MISSING, rig.clock.now(), None, None)


async def test_record_login_only_moves_a_passkey_counter_forward(rig: _Rig) -> None:
    device = await _passkey_device(rig)

    moved = await rig.store.record_login(device.id, rig.clock.now(), None, 4)
    with pytest.raises(PasskeyRejectedError):
        await rig.store.record_login(device.id, rig.clock.now(), None, 4)
    with pytest.raises(PasskeyRejectedError):
        await rig.store.record_login(device.id, rig.clock.now(), None, 2)

    assert moved.sign_count == 4
    assert (await rig.store.get_device(device.id)).sign_count == 4


# ──────────────────────────────────────────────────────────────────────────────
# LoginTable
# ──────────────────────────────────────────────────────────────────────────────


async def test_failures_count_up_one_at_a_time_and_clear_to_zero(rig: _Rig) -> None:
    logins, device = rig.store.logins, rig.device.id

    counts = [await logins.count_failure(device, rig.clock.now()) for _ in range(3)]
    before = await logins.failures(device)
    await logins.clear_failures(device)

    assert (counts, before, await logins.failures(device)) == ([1, 2, 3], 3, 0)
    with pytest.raises(InvariantViolationError):
        await logins.count_failure(_MISSING, rig.clock.now())


async def test_networks_are_remembered_once_each_in_canonical_form(rig: _Rig) -> None:
    logins, device = rig.store.logins, rig.device.id

    for network in ("100.64.3.0/24", "derp:nyc", "100.64.3.0/24"):
        await logins.remember_network(device, network, rig.clock.now())

    assert await logins.networks(device) == frozenset({"100.64.3.0/24", "derp:nyc"})
    assert await logins.networks(_MISSING) == frozenset()
    with pytest.raises(ValueError, match="canonical"):
        await logins.remember_network(device, "100.64.3.7/24", rig.clock.now())
    with pytest.raises(InvariantViolationError):
        await logins.remember_network(_MISSING, "derp:nyc", rig.clock.now())


# ──────────────────────────────────────────────────────────────────────────────
# PendingTable
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_held_request_reads_back_equal_with_its_event(rig: _Rig) -> None:
    pending = make_pending(rig.device.id, rig.clock)
    event = pending_event(pending, rig.clock)

    await rig.store.pending.put(pending, event)

    assert await rig.store.pending.get(pending.id) == pending
    assert [found.id for found in await rig.trail.query(TrailQuery())][-1] == event.id
    with pytest.raises(PendingNotFoundError):
        await rig.store.pending.get(make_pending(rig.device.id, rig.clock).id)


async def test_a_change_without_its_own_event_is_refused_and_writes_nothing(rig: _Rig) -> None:
    pending = make_pending(rig.device.id, rig.clock)
    other = make_pending(rig.device.id, rig.clock)

    with pytest.raises(InvariantViolationError):
        await rig.store.pending.put(pending, pending_event(other, rig.clock))
    await rig.store.pending.put(pending, pending_event(pending, rig.clock))
    wrong_kind = pending_event(pending, rig.clock, settled_as=_P.CANCELLED)
    confirmed = Settlement(_P.CONFIRMED, rig.clock.now(), confirmed_by=rig.device.id)
    with pytest.raises(InvariantViolationError):
        await rig.store.pending.settle(pending.id, _P.PENDING, confirmed, wrong_kind)

    assert (await rig.store.pending.get(pending.id)).status is _P.PENDING
    with pytest.raises(PendingNotFoundError):
        await rig.store.pending.get(other.id)


async def test_put_refuses_a_settled_request_a_taken_id_and_an_unknown_device(rig: _Rig) -> None:
    pending = make_pending(rig.device.id, rig.clock)
    await rig.store.pending.put(pending, pending_event(pending, rig.clock))
    settled = make_pending(rig.device.id, rig.clock, status=_P.EXPIRED, settled_at=rig.clock.now())

    for refused in (settled, pending, make_pending(_MISSING, rig.clock)):
        with pytest.raises(InvariantViolationError):
            await rig.store.pending.put(refused, pending_event(refused, rig.clock))


async def test_a_request_is_settled_once_from_the_expected_status(rig: _Rig) -> None:
    pending = make_pending(rig.device.id, rig.clock)
    await rig.store.pending.put(pending, pending_event(pending, rig.clock))
    confirmed = Settlement(_P.CONFIRMED, rig.clock.now(), confirmed_by=rig.device.id)

    settled = await rig.store.pending.settle(
        pending.id, _P.PENDING, confirmed, pending_event(pending, rig.clock, _P.CONFIRMED)
    )

    assert (settled.status, settled.confirmed_by) == (_P.CONFIRMED, rig.device.id)
    with pytest.raises(PendingStatusConflictError):
        await rig.store.pending.settle(
            pending.id, _P.PENDING, confirmed, pending_event(pending, rig.clock, _P.CONFIRMED)
        )
    with pytest.raises(InvalidPendingTransitionError):
        await rig.store.pending.settle(
            pending.id,
            _P.CONFIRMED,
            Settlement(_P.EXPIRED, rig.clock.now()),
            pending_event(pending, rig.clock, _P.EXPIRED),
        )
    assert await rig.store.pending.get(pending.id) == settled


async def test_a_confirmation_without_its_confirming_device_is_refused(rig: _Rig) -> None:
    pending = make_pending(rig.device.id, rig.clock)
    await rig.store.pending.put(pending, pending_event(pending, rig.clock))

    with pytest.raises(ValidationError):
        await rig.store.pending.settle(
            pending.id,
            _P.PENDING,
            Settlement(_P.CONFIRMED, rig.clock.now()),
            pending_event(pending, rig.clock, _P.CONFIRMED),
        )

    assert (await rig.store.pending.get(pending.id)).status is _P.PENDING


async def test_list_by_status_filters_and_orders_oldest_first(rig: _Rig) -> None:
    first = make_pending(rig.device.id, rig.clock)
    rig.clock.advance(1)
    second = make_pending(rig.device.id, rig.clock)
    for pending in (second, first):
        await rig.store.pending.put(pending, pending_event(pending, rig.clock))
    cancelled = Settlement(_P.CANCELLED, rig.clock.now())
    await rig.store.pending.settle(
        first.id, _P.PENDING, cancelled, pending_event(first, rig.clock, _P.CANCELLED)
    )

    held = await rig.store.pending.list_by_status(_P.PENDING)
    everything = await rig.store.pending.list_by_status()

    assert held == (second,)
    assert [pending.id for pending in everything] == [first.id, second.id]


# ──────────────────────────────────────────────────────────────────────────────
# ModeTable
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_mode_is_open_until_a_change_is_written_with_its_event(rig: _Rig) -> None:
    initial = await rig.store.entrance_mode.get()
    event = _mode_event(rig, "guard.reduced")

    await rig.store.entrance_mode.change(EntranceMode.OPEN, EntranceMode.REDUCED, event)

    assert (initial, await rig.store.entrance_mode.get()) == (
        EntranceMode.OPEN,
        EntranceMode.REDUCED,
    )
    assert [found.id for found in await rig.trail.query(TrailQuery(kind="guard.reduced"))] == [
        event.id
    ]


async def test_a_refused_mode_change_writes_neither_the_mode_nor_its_event(rig: _Rig) -> None:
    reopened = _mode_event(rig, "guard.reopened")

    with pytest.raises(EntranceModeConflictError):
        await rig.store.entrance_mode.change(EntranceMode.REDUCED, EntranceMode.OPEN, reopened)
    with pytest.raises(InvalidModeTransitionError):
        await rig.store.entrance_mode.change(EntranceMode.OPEN, EntranceMode.OPEN, reopened)
    with pytest.raises(InvariantViolationError):
        await rig.store.entrance_mode.change(EntranceMode.OPEN, EntranceMode.REDUCED, reopened)

    assert await rig.store.entrance_mode.get() is EntranceMode.OPEN
    assert await rig.trail.query(TrailQuery(kind="guard.reopened")) == ()
