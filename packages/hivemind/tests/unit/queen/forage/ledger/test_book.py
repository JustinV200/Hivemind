"""Tests for hivemind.queen.forage.ledger.book: ForageLedger.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/book.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.book for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.forage import make_capacity, make_grant, make_reserve

from hivemind.forage.grant_state import GrantState
from hivemind.queen.forage.ledger import ForageLedger, InMemoryLedgerStore, LocalPoolReport
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id


def test_headroom_is_zero_with_no_capacity_reported() -> None:
    ledger = ForageLedger()

    assert ledger.headroom().sub_bees == 0


def test_report_capacity_increases_headroom() -> None:
    ledger = ForageLedger(reserve=make_reserve(seats=0, headroom_fraction=0.0))
    clock = FakeClock()
    cell_id = new_cell_id(clock)

    asyncio.run(ledger.report_capacity(cell_id, make_capacity(max_sub_bees=4)))

    assert ledger.headroom().sub_bees == 4
    capacity = ledger.capacity_for(cell_id)
    assert capacity is not None
    assert capacity.max_sub_bees == 4


def test_headroom_subtracts_the_reserves_seats_and_fraction() -> None:
    ledger = ForageLedger(reserve=make_reserve(seats=1, headroom_fraction=0.5))
    clock = FakeClock()
    asyncio.run(ledger.report_capacity(new_cell_id(clock), make_capacity(max_sub_bees=10)))

    # 10 * (1 - 0.5) = 5, then - 1 reserved seat = 4.
    assert ledger.headroom().sub_bees == 4


def test_record_grant_reduces_headroom_by_its_max_sub_bees() -> None:
    ledger = ForageLedger(reserve=make_reserve(seats=0, headroom_fraction=0.0))
    clock = FakeClock()
    asyncio.run(ledger.report_capacity(new_cell_id(clock), make_capacity(max_sub_bees=10)))
    grant = make_grant(clock=clock, state=GrantState.ACTIVE, max_sub_bees=3)

    asyncio.run(ledger.record_grant(grant))

    assert ledger.headroom().sub_bees == 7
    assert ledger.live_grants() == (grant,)


def test_set_reserve_replaces_the_reserve_used_by_headroom() -> None:
    ledger = ForageLedger(reserve=make_reserve(seats=0, headroom_fraction=0.0))
    clock = FakeClock()
    asyncio.run(ledger.report_capacity(new_cell_id(clock), make_capacity(max_sub_bees=10)))
    assert ledger.headroom().sub_bees == 10

    asyncio.run(ledger.set_reserve(make_reserve(seats=2, headroom_fraction=0.0)))

    assert ledger.reserve.seats == 2
    assert ledger.headroom().sub_bees == 8


def test_headroom_never_goes_negative() -> None:
    ledger = ForageLedger(reserve=make_reserve(seats=5, headroom_fraction=0.0))
    clock = FakeClock()
    asyncio.run(ledger.report_capacity(new_cell_id(clock), make_capacity(max_sub_bees=1)))

    assert ledger.headroom().sub_bees == 0


def test_record_grant_with_a_terminal_state_removes_it_from_live_grants() -> None:
    ledger = ForageLedger()
    clock = FakeClock()
    grant = make_grant(clock=clock, state=GrantState.ACTIVE)
    asyncio.run(ledger.record_grant(grant))
    assert ledger.live_grants() == (grant,)

    revoked = grant.model_copy(update={"state": GrantState.REVOKED})
    asyncio.run(ledger.record_grant(revoked))

    assert ledger.live_grants() == ()
    assert ledger.grant(grant.id) is None


def test_grants_for_filters_by_holder() -> None:
    ledger = ForageLedger()
    clock = FakeClock()
    holder = new_warden_id(clock)
    mine = make_grant(clock=clock, holder=holder, state=GrantState.ACTIVE)
    other = make_grant(clock=clock, state=GrantState.ACTIVE)
    asyncio.run(ledger.record_grant(mine))
    asyncio.run(ledger.record_grant(other))

    assert ledger.grants_for(holder) == (mine,)


def test_report_local_pool_is_reported_never_granted() -> None:
    # codingrules 8.10: "local pools appear in the ledger as reported, not granted."
    ledger = ForageLedger()
    clock = FakeClock()
    report = LocalPoolReport(
        warden_id=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        sub_bees_active=2,
        model_vram_bytes=0,
        model_disk_bytes=0,
        seats_exported=1,
    )

    asyncio.run(ledger.report_local_pool(report))

    # Reporting local usage never moves shared headroom: it is bookkeeping, not a grant.
    assert ledger.headroom().sub_bees == 0


async def test_restore_rebuilds_every_table_from_the_store() -> None:
    store = InMemoryLedgerStore()
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    grant = make_grant(clock=clock, state=GrantState.ACTIVE)
    original = ForageLedger(store=store)
    await original.set_reserve(make_reserve(seats=0, headroom_fraction=0.0))
    await original.report_capacity(cell_id, make_capacity(max_sub_bees=6))
    await original.record_grant(grant)

    restored = ForageLedger(store=store)
    await restored.restore()

    assert restored.capacity_for(cell_id) == original.capacity_for(cell_id)
    assert restored.grant(grant.id) == grant
    assert restored.headroom().sub_bees == 6 - grant.max_sub_bees


async def test_restore_is_a_no_op_with_no_store() -> None:
    ledger = ForageLedger()

    await ledger.restore()  # Must not raise.

    assert ledger.live_grants() == ()
