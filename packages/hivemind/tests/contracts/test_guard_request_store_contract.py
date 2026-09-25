"""Contract suite for GuardRequestStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.queen.guard_requests.protocol.GuardRequestStore contract and runs against both
    implementations that ship: InMemoryGuardRequestStore and SqliteGuardRequestStore (on a tmp_path
    SQLite file). A new implementation joins the fixture's params and must pass here first
    (codingrules 14.3). The SQLite half also proves the door's durability: a request filed before
    the store is reopened is still there, undecided, after it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.guard_requests.protocol for the protocol under test.
    - tests/contracts/test_goal_request_store_contract.py for the pattern mirrored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.isolation import make_decision, make_guard_request, make_hold

from hivemind.common.sqlite import connect
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.guard_requests import (
    GuardRequestStore,
    InMemoryGuardRequestStore,
    SqliteGuardRequestStore,
)
from waggle.clock import FakeClock

_STORE_KINDS = ("memory", "sqlite")


@pytest.fixture(params=_STORE_KINDS)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> GuardRequestStore:
    """A store of the parametrised kind."""
    if request.param == "memory":
        return InMemoryGuardRequestStore()
    return await SqliteGuardRequestStore.create(connect(tmp_path / "hive.sqlite3"), FakeClock())


async def test_a_filed_request_is_pending_until_decided(store: GuardRequestStore) -> None:
    clock = FakeClock()
    request = make_guard_request(clock)

    assert await store.file(request) is True
    assert await store.pending() == (request,)
    assert await store.get(request.id) == request


async def test_filing_one_report_twice_writes_it_once(store: GuardRequestStore) -> None:
    request = make_guard_request(FakeClock())
    await store.file(request)

    assert await store.file(request) is False
    assert len(await store.pending()) == 1


async def test_pending_is_oldest_first_and_bounded(store: GuardRequestStore) -> None:
    clock = FakeClock()
    older = make_guard_request(clock)
    clock.advance(5.0)
    newer = make_guard_request(clock)
    await store.file(newer)
    await store.file(older)

    assert [r.id for r in await store.pending()] == [older.id, newer.id]
    assert [r.id for r in await store.pending(limit=1)] == [older.id]


async def test_a_decision_is_stamped_once_and_leaves_pending(store: GuardRequestStore) -> None:
    clock = FakeClock()
    request = make_guard_request(clock)
    await store.file(request)
    first = make_decision(clock)

    decided = await store.decide(request.id, first)
    again = await store.decide(request.id, make_decision(clock, QueenAction.DISMISS))

    assert decided is not None and decided.decision == first
    assert again is not None and again.decision == first  # The first decision stands.
    assert await store.pending() == ()
    assert await store.decide("guardrep_01ARZ3NDEKTSV4RRFFQ69G5FAV", first) is None


async def test_a_hold_is_active_until_its_cells_lift_releases_it(store: GuardRequestStore) -> None:
    clock = FakeClock()
    held, other = make_guard_request(clock), make_guard_request(clock)
    for request in (held, other):
        await store.file(request)
    hold = make_hold(held, clock)
    await store.decide(held.id, make_decision(clock), hold)
    await store.decide(other.id, make_decision(clock))  # Decided, but it held nothing.

    assert await store.holds() == (hold,)
    clock.advance(1.0)
    released = await store.release_holds(hold.cell_id, clock.now())

    assert [done.report_id for done in released] == [held.id]
    assert released[0].released_at == clock.now()
    assert await store.holds() == ()
    assert await store.release_holds(hold.cell_id, clock.now()) == ()  # Released once only.


async def test_a_filed_request_survives_reopening_the_sqlite_store(tmp_path: Path) -> None:
    clock = FakeClock()
    db = tmp_path / "hive.sqlite3"
    request = make_guard_request(clock)
    await (await SqliteGuardRequestStore.create(connect(db), clock)).file(request)

    reopened = await SqliteGuardRequestStore.create(connect(db), clock)

    # The door's "durable before it returns": a restart finds it still waiting for a decision.
    assert await reopened.pending() == (request,)
