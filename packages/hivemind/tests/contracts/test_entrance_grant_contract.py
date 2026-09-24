"""Contract suite for EntranceStore.update_device_grant: re-granting an approved device.

A re-grant replaces an APPROVED device's capability set (and, when given, its daily cap) and records
its ``guard.entrance_approved`` event in the same step; a device in any other status, or an event
of another kind or about another device, is refused with nothing written. Split from
``test_entrance_store_contract.py`` for codingrules 5.1's size limit only.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Runs against
    hivemind.entrance.store.memory.MemoryEntranceStore and
    hivemind.entrance.store.sqlite.SqliteEntranceStore (a tmp_path SQLite file shared with a
    SqlitePheromoneTrail), codingrules 14.3.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.protocol for the protocol under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.entrance import STORE_IDENTITY, entry_event, make_device, walk_to

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.entrance.enrol import APPROVED_TRAIL_KIND, DeviceStatus, EnrolledDevice
from hivemind.entrance.errors import DeviceStatusConflictError
from hivemind.entrance.store import EntranceStore, MemoryEntranceStore, SqliteEntranceStore
from hivemind.pheromone import (
    GuardEvent,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
from waggle.clock import FakeClock


@dataclass(frozen=True, slots=True)
class _Tables:
    """An EntranceStore, the trail it records on, and the clock both share."""

    store: EntranceStore
    trail: PheromoneTrail
    clock: FakeClock


@pytest.fixture(params=("memory", "sqlite"))
async def tables(request: pytest.FixtureRequest, tmp_path: Path) -> _Tables:
    """Empty Entrance tables of the parametrised kind."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _Tables(MemoryEntranceStore(memory_trail), memory_trail, clock)
    db = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db), clock)
    return _Tables(await SqliteEntranceStore.create(connect(db), clock), sqlite_trail, clock)


async def _device(tables: _Tables, status: DeviceStatus) -> EnrolledDevice:
    """Put a device and walk it to ``status`` through the store's own edges."""
    device = make_device(tables.clock)
    await tables.store.put_device(device, entry_event(device, tables.clock))
    return await walk_to(tables.store, device, status, tables.clock)


def _approval(
    device: EnrolledDevice, clock: FakeClock, kind: str = APPROVED_TRAIL_KIND
) -> GuardEvent:
    """A re-grant's event about ``device``."""
    return STORE_IDENTITY.event(clock, kind, device.id, {"regrant": True})


async def _approvals(tables: _Tables, device: EnrolledDevice) -> int:
    """How many approvals of ``device`` the trail holds."""
    query = TrailQuery(kind=APPROVED_TRAIL_KIND, subject_id=device.id)
    return len(await tables.trail.query(query))


async def test_a_regrant_replaces_the_set_and_cap_with_its_event(tables: _Tables) -> None:
    device = await _device(tables, DeviceStatus.APPROVED)
    event = _approval(device, tables.clock)

    updated = await tables.store.update_device_grant(
        device.id, event, capabilities=("observe",), spend_cap_usd_per_day=0.5
    )

    assert (updated.capabilities, updated.spend_cap_usd_per_day) == (("observe",), 0.5)
    assert await tables.store.get_device(device.id) == updated
    recorded = await tables.trail.query(TrailQuery(kind=APPROVED_TRAIL_KIND, subject_id=device.id))
    assert recorded[-1].id == event.id


async def test_a_regrant_of_a_device_not_approved_writes_nothing(tables: _Tables) -> None:
    device = await _device(tables, DeviceStatus.LOCKED)
    before = await _approvals(tables, device)

    with pytest.raises(DeviceStatusConflictError):
        await tables.store.update_device_grant(
            device.id, _approval(device, tables.clock), capabilities=("observe",)
        )

    assert await tables.store.get_device(device.id) == device
    assert await _approvals(tables, device) == before


async def test_a_regrant_with_another_kind_of_event_is_refused(tables: _Tables) -> None:
    device = await _device(tables, DeviceStatus.APPROVED)
    event = _approval(device, tables.clock, kind="guard.entrance_denied")

    with pytest.raises(InvariantViolationError):
        await tables.store.update_device_grant(device.id, event, capabilities=("observe",))

    assert await tables.store.get_device(device.id) == device
