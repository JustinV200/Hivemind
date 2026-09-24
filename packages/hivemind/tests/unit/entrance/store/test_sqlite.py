"""Tests for hivemind.entrance.store.sqlite: migrations and durability of the Entrance tables.

What every EntranceStore does is in tests/contracts/test_entrance_store_contract.py; this module
covers what only the SQLite store does: its own migration series in the shared schema table, and
state that survives a new connection to the same file (a Queen restart).

Fits into the Hive:
    Mirrors src/hivemind/entrance/store/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.sqlite for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.entrance import make_device, make_invite, walk_to

from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.store import SUBSYSTEM, SqliteEntranceStore, apply_entrance_migrations
from waggle.clock import FakeClock

_PHC = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA"
)


async def test_create_records_the_entrance_series_once(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    await SqliteEntranceStore.create(connect(db), clock)
    connection = connect(db)

    applied_again = apply_entrance_migrations(connection, clock)

    assert SUBSYSTEM == "entrance"
    assert applied_versions(connection, SUBSYSTEM) == (1,)
    assert applied_again == ()


async def test_everything_written_survives_a_new_connection(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    store = await SqliteEntranceStore.create(connect(db), clock)
    device = make_device(clock)
    await store.put_device(device)
    invite = make_invite(device, clock)
    await store.put_invite(invite)
    await store.set_operator_password_hash(_PHC, clock.now())
    approved = await walk_to(store, device, DeviceStatus.APPROVED, clock)

    reopened = await SqliteEntranceStore.create(connect(db), clock)

    assert await reopened.get_device(device.id) == approved
    assert await reopened.list_devices(DeviceStatus.APPROVED) == (approved,)
    assert await reopened.get_invite(invite.code_hash) == invite
    operator = await reopened.get_operator()
    assert operator is not None
    assert operator.password_hash == _PHC


async def test_the_status_column_follows_every_change(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    store = await SqliteEntranceStore.create(connect(db), clock)
    device = make_device(clock)
    await store.put_device(device)

    await walk_to(store, device, DeviceStatus.LOCKED, clock)

    row = connect(db).execute("SELECT status FROM entrance_devices WHERE id = ?", (device.id,))
    assert row.fetchone()["status"] == "LOCKED"
