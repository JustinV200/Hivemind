"""Tests for hivemind.queen.forage.ledger.store_memory: InMemoryLedgerStore.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/store_memory.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.store_memory for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity, make_grant, make_reserve
from pydantic import ValidationError

from hivemind.forage.grant_state import GrantState
from hivemind.queen.forage.ledger import InMemoryLedgerStore, LocalPoolReport
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id


async def test_put_capacity_upserts_by_cell_id() -> None:
    store = InMemoryLedgerStore()
    clock = FakeClock()
    cell_id = new_cell_id(clock)

    await store.put_capacity(cell_id, make_capacity(max_sub_bees=1))
    await store.put_capacity(cell_id, make_capacity(max_sub_bees=9))

    rows = await store.list_capacities()
    assert rows == ((cell_id, make_capacity(max_sub_bees=9)),)


async def test_put_local_report_upserts_by_warden_id() -> None:
    store = InMemoryLedgerStore()
    clock = FakeClock()
    report = LocalPoolReport(
        warden_id=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        sub_bees_active=1,
        model_vram_bytes=0,
        model_disk_bytes=0,
        seats_exported=0,
    )

    await store.put_local_report(report)
    await store.put_local_report(report.model_copy(update={"sub_bees_active": 3}))

    rows = await store.list_local_reports()
    assert rows == (report.model_copy(update={"sub_bees_active": 3}),)


async def test_put_grant_upserts_and_delete_grant_is_idempotent() -> None:
    store = InMemoryLedgerStore()
    clock = FakeClock()
    grant = make_grant(clock=clock, state=GrantState.ACTIVE)

    await store.put_grant(grant)
    assert await store.list_grants() == (grant,)

    await store.delete_grant(grant.id)
    assert await store.list_grants() == ()
    await store.delete_grant(grant.id)  # A second delete of an unknown id is a no-op.
    assert await store.list_grants() == ()


async def test_reserve_is_none_until_put_then_returns_the_latest() -> None:
    store = InMemoryLedgerStore()

    assert await store.get_reserve() is None

    await store.put_reserve(make_reserve(seats=3))
    await store.put_reserve(make_reserve(seats=7))

    restored = await store.get_reserve()
    assert restored is not None
    assert restored.seats == 7


async def test_measured_grades_are_empty_until_put_and_upsert_by_source_id() -> None:
    store = InMemoryLedgerStore()
    clock = FakeClock()
    assert await store.list_measured_grades() == ()

    await store.put_measured_grade("fast-local", 2, clock.now(), "first run")
    await store.put_measured_grade("fast-local", 4, clock.now(), "second run")
    await store.put_measured_grade("big-hosted", 5, clock.now(), "other")

    rows = {row.source_id: row for row in await store.list_measured_grades()}
    assert {key: row.grade for key, row in rows.items()} == {"fast-local": 4, "big-hosted": 5}
    assert rows["fast-local"].evidence == "second run"


@pytest.mark.parametrize("grade", [0, 6, -1])
async def test_put_measured_grade_refuses_a_grade_outside_one_to_five(grade: int) -> None:
    store = InMemoryLedgerStore()

    with pytest.raises(ValidationError):
        await store.put_measured_grade("fast-local", grade, FakeClock().now(), "bad")

    assert await store.list_measured_grades() == ()
