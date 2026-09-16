"""Tests for hivemind.queen.forage.ledger.decisions: DecisionBook.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/decisions.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.decisions for the module under test.
"""

from __future__ import annotations

from hivemind.forage.models.pools import Ceilings, HostingPlan, SlotPlan, SourceChain
from hivemind.forage.slots import ModelSlot
from hivemind.queen.forage.ledger.decisions import DecisionBook
from hivemind.queen.forage.ledger.store_memory import InMemoryLedgerStore
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id


def _plan(**overrides: object) -> HostingPlan:
    fields: dict[str, object] = {
        "cell_id": new_cell_id(FakeClock()),
        "slots": (SlotPlan(slot=ModelSlot.WORKER, chain=SourceChain(primary="src_primary")),),
        "default": SourceChain(primary="src_primary"),
        "reason": "local model covers it",
    }
    fields.update(overrides)
    return HostingPlan(**fields)


def _ceilings(**overrides: object) -> Ceilings:
    fields: dict[str, object] = {
        "max_sub_bees": 4,
        "model_vram_bytes": 1_000,
        "model_disk_bytes": 2_000,
        "loadable_sources": (),
        "exportable_seats": 1,
    }
    fields.update(overrides)
    return Ceilings(**fields)


async def test_plan_for_is_none_before_any_plan_is_recorded() -> None:
    book = DecisionBook(store=None)

    assert book.plan_for(new_cell_id(FakeClock())) is None


async def test_record_plan_replaces_the_prior_revision_for_the_same_cell() -> None:
    book = DecisionBook(store=None)
    cell_id = new_cell_id(FakeClock())
    await book.record_plan(_plan(cell_id=cell_id, revision=0))

    await book.record_plan(_plan(cell_id=cell_id, revision=1))

    stored = book.plan_for(cell_id)
    assert stored is not None
    assert stored.revision == 1


async def test_ceilings_for_is_none_before_any_ceilings_are_recorded() -> None:
    book = DecisionBook(store=None)

    assert book.ceilings_for(new_warden_id(FakeClock())) is None


async def test_record_ceilings_returns_zero_the_first_time_and_increments_after() -> None:
    book = DecisionBook(store=None)
    holder = new_warden_id(FakeClock())

    first_revision = await book.record_ceilings(holder, _ceilings())
    second_revision = await book.record_ceilings(holder, _ceilings(max_sub_bees=8))

    assert first_revision == 0
    assert second_revision == 1
    stored = book.ceilings_for(holder)
    assert stored is not None
    assert stored.max_sub_bees == 8


async def test_record_ceilings_revision_is_independent_per_holder() -> None:
    book = DecisionBook(store=None)
    first_holder = new_warden_id(FakeClock())
    second_holder = new_warden_id(FakeClock())
    await book.record_ceilings(first_holder, _ceilings())
    await book.record_ceilings(first_holder, _ceilings())

    revision = await book.record_ceilings(second_holder, _ceilings())

    assert revision == 0


async def test_restore_rebuilds_both_plans_and_ceilings() -> None:
    store = InMemoryLedgerStore()
    cell_id = new_cell_id(FakeClock())
    holder = new_warden_id(FakeClock())
    original = DecisionBook(store)
    await original.record_plan(_plan(cell_id=cell_id))
    await original.record_ceilings(holder, _ceilings())

    restored = DecisionBook(store)
    await restored.restore()

    assert restored.plan_for(cell_id) == original.plan_for(cell_id)
    assert restored.ceilings_for(holder) == original.ceilings_for(holder)


async def test_restore_is_a_no_op_with_no_store() -> None:
    book = DecisionBook(store=None)

    await book.restore()  # Must not raise.

    assert book.plan_for(new_cell_id(FakeClock())) is None
