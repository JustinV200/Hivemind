"""Contract suite for SessionTable: one contract, run over every implementation.

Sessions and the nonces their requests spend (roadmap 10.5e, ADR-0033): a session is put open and
once, ends once and is never touched again, and a nonce is granted once until it expires. The suite
runs over ``EntranceStore.sessions`` of both stores and over ``SplitSessionTable``, which also
proves the console's volatile sessions stay out of the durable table.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.entrance.store.sessions.protocol.SessionTable contract and runs against
    MemorySessionTable (through MemoryEntranceStore), SqliteSessionTable (through
    SqliteEntranceStore, over a tmp_path file shared with a SqlitePheromoneTrail) and
    SplitSessionTable over the SQLite one (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.store.sessions.protocol for the protocol under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from builders.entrance import entry_event, make_device, make_session

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.entrance.auth import EndReason, Listener
from hivemind.entrance.auth.session import NonceClaim
from hivemind.entrance.store import (
    EntranceStore,
    MemoryEntranceStore,
    MemorySessionTable,
    SessionTable,
    SplitSessionTable,
    SqliteEntranceStore,
)
from hivemind.pheromone import MemoryPheromoneTrail, SqlitePheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import DeviceId

_KINDS = ("memory", "sqlite", "split")
_MISSING = DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")
_NONCE = "bm9uY2Utb2YtYXQtbGVhc3Qtc2l4dGVlbi1ieXRlcw"  # A canonical base64url nonce.


@dataclass(frozen=True, slots=True)
class _Rig:
    """A session table, the store whose devices it names, a device, and the clock."""

    table: SessionTable
    store: EntranceStore
    device: DeviceId
    clock: FakeClock


@pytest.fixture(params=_KINDS)
async def rig(request: pytest.FixtureRequest, tmp_path: Path) -> _Rig:
    """A session table of the parametrised kind, over a store holding one device."""
    clock = FakeClock()
    store: EntranceStore
    if request.param == "memory":
        store = MemoryEntranceStore(MemoryPheromoneTrail(clock))
    else:
        db = tmp_path / "hive.sqlite3"
        await SqlitePheromoneTrail.create(connect(db), clock)
        store = await SqliteEntranceStore.create(connect(db), clock)
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))
    table: SessionTable = store.sessions
    if request.param == "split":
        table = SplitSessionTable(store.sessions, MemorySessionTable())
    return _Rig(table, store, device.id, clock)


async def test_a_put_session_reads_back_equal_and_an_unknown_hash_reads_none(rig: _Rig) -> None:
    session = make_session(rig.device, rig.clock, stepped_up_until=rig.clock.now())

    await rig.table.put(session)

    assert await rig.table.get(session.token_hash) == session
    assert await rig.table.get("0" * 64) is None


async def test_put_refuses_an_ended_session_a_taken_hash_and_an_unknown_device(rig: _Rig) -> None:
    session = make_session(rig.device, rig.clock)
    await rig.table.put(session)
    ended = make_session(
        rig.device, rig.clock, ended_at=rig.clock.now(), end_reason=EndReason.LOGOUT
    )

    with pytest.raises(InvariantViolationError):
        await rig.table.put(ended)
    with pytest.raises(InvariantViolationError):
        await rig.table.put(session)
    with pytest.raises(InvariantViolationError):
        await rig.table.put(make_session(_MISSING, rig.clock))
    assert await rig.table.get(ended.token_hash) is None


async def test_touch_only_moves_last_seen_forward_on_an_open_session(rig: _Rig) -> None:
    session = make_session(rig.device, rig.clock)
    await rig.table.put(session)
    later = rig.clock.now() + timedelta(minutes=5)

    await rig.table.touch(session.token_hash, later)
    await rig.table.touch(session.token_hash, rig.clock.now())

    stored = await rig.table.get(session.token_hash)
    assert stored is not None
    assert stored.last_seen_at == later


async def test_step_up_marks_the_window_and_clears_the_travel_flag(rig: _Rig) -> None:
    session = make_session(rig.device, rig.clock, needs_step_up=True)
    await rig.table.put(session)
    until = rig.clock.now() + timedelta(minutes=5)

    stepped = await rig.table.step_up(session.token_hash, until)

    assert stepped is not None
    assert (stepped.stepped_up_until, stepped.needs_step_up) == (until, False)
    assert await rig.table.get(session.token_hash) == stepped
    assert await rig.table.step_up("0" * 64, until) is None


async def test_a_session_ends_once_and_is_never_changed_again(rig: _Rig) -> None:
    session = make_session(rig.device, rig.clock)
    await rig.table.put(session)
    now = rig.clock.now()

    first = await rig.table.end(session.token_hash, now, EndReason.LOGOUT)
    again = await rig.table.end(session.token_hash, now, EndReason.IDLE)
    await rig.table.touch(session.token_hash, now + timedelta(minutes=1))
    stepped = await rig.table.step_up(session.token_hash, now + timedelta(minutes=5))

    stored = await rig.table.get(session.token_hash)
    assert (first, again, stepped) == (True, False, None)
    assert stored is not None
    assert (stored.ended_at, stored.end_reason, stored.last_seen_at) == (
        now,
        EndReason.LOGOUT,
        session.last_seen_at,
    )


async def test_end_for_device_ends_only_that_devices_open_sessions(rig: _Rig) -> None:
    other = make_device(rig.clock)
    await rig.store.put_device(other, entry_event(other, rig.clock))
    mine = [make_session(rig.device, rig.clock) for _ in range(2)]
    theirs = make_session(other.id, rig.clock)
    for session in (*mine, theirs):
        await rig.table.put(session)

    ended = await rig.table.end_for_device(rig.device, rig.clock.now(), EndReason.REVOKED)

    assert ended == 2
    assert [session.token_hash for session in await rig.table.list_open()] == [theirs.token_hash]


async def test_end_for_listener_ends_only_that_listeners_sessions(rig: _Rig) -> None:
    remote = make_session(rig.device, rig.clock)
    loopback = make_session(rig.device, rig.clock, listener=Listener.LOOPBACK)
    await rig.table.put(remote)
    await rig.table.put(loopback)

    ended = await rig.table.end_for_listener(Listener.REMOTE, rig.clock.now(), EndReason.REDUCED)

    stored = await rig.table.get(remote.token_hash)
    assert ended == 1
    assert stored is not None
    assert stored.end_reason is EndReason.REDUCED
    assert await rig.table.list_open() == (loopback,)


async def test_list_open_returns_open_sessions_oldest_first(rig: _Rig) -> None:
    first = make_session(rig.device, rig.clock)
    rig.clock.advance(1)
    second = make_session(rig.device, rig.clock)
    for session in (second, first):
        await rig.table.put(session)

    assert await rig.table.list_open() == (first, second)


async def test_a_nonce_is_granted_once_until_it_expires(rig: _Rig) -> None:
    now = rig.clock.now()
    claim = NonceClaim(_NONCE, "a" * 64, now + timedelta(seconds=120), now)
    later = now + timedelta(seconds=120)

    first = await rig.table.claim_nonce(claim)
    replayed = await rig.table.claim_nonce(claim)
    after_expiry = await rig.table.claim_nonce(
        NonceClaim(_NONCE, "a" * 64, later + timedelta(seconds=120), later)
    )

    assert (first, replayed, after_expiry) == (True, False, True)


async def test_split_keeps_a_console_session_out_of_the_durable_table(tmp_path: Path) -> None:
    clock = FakeClock()
    db = tmp_path / "hive.sqlite3"
    await SqlitePheromoneTrail.create(connect(db), clock)
    store = await SqliteEntranceStore.create(connect(db), clock)
    device = make_device(clock)
    await store.put_device(device, entry_event(device, clock))
    split = SplitSessionTable(store.sessions, MemorySessionTable())
    console = make_session(device.id, clock, volatile=True, listener=Listener.LOOPBACK)

    await split.put(console)

    assert await split.get(console.token_hash) == console
    assert await store.sessions.get(console.token_hash) is None
    with pytest.raises(InvariantViolationError, match="never persisted"):
        await store.sessions.put(console)
    assert await split.end_for_device(device.id, clock.now(), EndReason.LOCKED) == 1
