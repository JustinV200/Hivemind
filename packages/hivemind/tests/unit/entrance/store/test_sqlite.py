"""Tests for hivemind.entrance.store.sqlite: migrations and durability of the Entrance tables.

What every EntranceStore does is in tests/contracts/test_entrance_store_contract.py (and the
10.5e tables' two suites beside it); this module covers what only the SQLite store does: its own
migration series in the shared schema table, its refusal of a file without the Pheromone Trail's
table, and state (with its trail events) that survives a new connection to the same file (a Queen
restart), the sessions, spent nonces, login failures, networks, held requests and mode included.

Fits into the Hive:
    Mirrors src/hivemind/entrance/store/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.sqlite for the module under test.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from builders.entrance import (
    STORE_IDENTITY,
    entry_event,
    make_device,
    make_invite,
    make_pending,
    make_session,
    pending_event,
    walk_to,
)

from hivemind.common.errors import MigrationError
from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.entrance.auth.session import NonceClaim
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.reducer import EntranceMode
from hivemind.entrance.store import SUBSYSTEM, SqliteEntranceStore, apply_entrance_migrations
from hivemind.pheromone import SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock

_PHC = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA"
)


async def _store(db: Path, clock: FakeClock) -> SqliteEntranceStore:
    """Create the trail's table and then the Entrance's, as a composition root does."""
    await SqlitePheromoneTrail.create(connect(db), clock)
    return await SqliteEntranceStore.create(connect(db), clock)


async def test_create_refuses_a_file_without_the_trails_table(tmp_path: Path) -> None:
    connection = connect(tmp_path / "hive.sqlite3")

    with pytest.raises(MigrationError, match="pheromone_events"):
        await SqliteEntranceStore.create(connection, FakeClock())

    assert applied_versions(connection, SUBSYSTEM) == ()


async def test_create_records_the_entrance_series_once(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    await _store(db, clock)
    connection = connect(db)

    applied_again = apply_entrance_migrations(connection, clock)

    assert SUBSYSTEM == "entrance"
    assert applied_versions(connection, SUBSYSTEM) == (1, 2)
    assert applied_again == ()


async def test_everything_written_survives_a_new_connection(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    store = await _store(db, clock)
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))
    invite = make_invite(device, clock)
    await store.put_invite(invite)
    await store.set_operator_password_hash(_PHC, clock.now())
    approved = await walk_to(store, device, DeviceStatus.APPROVED, clock)

    reopened = await SqliteEntranceStore.create(connect(db), clock)
    trail = await SqlitePheromoneTrail.create(connect(db), clock)

    assert await reopened.get_device(device.id) == approved
    assert await reopened.list_devices(DeviceStatus.APPROVED) == (approved,)
    assert await reopened.get_invite(invite.code_hash) == invite
    operator = await reopened.get_operator()
    assert operator is not None
    assert operator.password_hash == _PHC
    kinds = [event.kind for event in await trail.query(TrailQuery(subject_id=device.id))]
    assert kinds == ["guard.entrance_invited", "guard.entrance_pending", "guard.entrance_approved"]


async def test_the_status_column_follows_every_change(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    store = await _store(db, clock)
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))

    await walk_to(store, device, DeviceStatus.LOCKED, clock)

    row = connect(db).execute("SELECT status FROM entrance_devices WHERE id = ?", (device.id,))
    assert row.fetchone()["status"] == "LOCKED"


async def test_the_login_session_and_mode_tables_survive_a_new_connection(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    clock = FakeClock()
    store = await _store(db, clock)
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))
    session, pending = make_session(device.id, clock), make_pending(device.id, clock)
    later = clock.now() + timedelta(seconds=120)
    claim = NonceClaim("bm9uY2Utb25lLW5vbmNlLW9uZQ", session.token_hash, later, clock.now())
    reduced = STORE_IDENTITY.event(clock, "guard.reduced", STORE_IDENTITY.hive_id, {})
    await store.sessions.put(session)
    await store.sessions.claim_nonce(claim)
    await store.logins.count_failure(device.id, clock.now())
    await store.logins.remember_network(device.id, "derp:nyc", clock.now())
    await store.pending.put(pending, pending_event(pending, clock))
    await store.entrance_mode.change(EntranceMode.OPEN, EntranceMode.REDUCED, reduced)

    reopened = await SqliteEntranceStore.create(connect(db), clock)

    assert await reopened.sessions.get(session.token_hash) == session
    assert not await reopened.sessions.claim_nonce(claim)
    assert await reopened.logins.failures(device.id) == 1
    assert await reopened.logins.networks(device.id) == frozenset({"derp:nyc"})
    assert await reopened.pending.get(pending.id) == pending
    assert await reopened.entrance_mode.get() is EntranceMode.REDUCED
