"""Tests for hivemind.queen.forage.ledger.spend: SpendBook.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/spend.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.spend for the module under test.
"""

from __future__ import annotations

from hivemind.queen.forage.ledger.spend import SpendBook
from hivemind.queen.forage.ledger.store_memory import InMemoryLedgerStore
from waggle.clock import FakeClock
from waggle.ids import new_task_id


async def test_for_goal_is_zero_before_anything_is_recorded() -> None:
    book = SpendBook(store=None)
    goal_id = new_task_id(FakeClock())

    assert book.for_goal(goal_id) == 0.0


async def test_record_accumulates_across_calls() -> None:
    book = SpendBook(store=None)
    goal_id = new_task_id(FakeClock())

    await book.record(goal_id, 1.5)
    total = await book.record(goal_id, 2.5)

    assert total == 4.0
    assert book.for_goal(goal_id) == 4.0


async def test_headroom_is_cap_minus_spend() -> None:
    book = SpendBook(store=None)
    goal_id = new_task_id(FakeClock())
    await book.record(goal_id, 3.0)

    assert book.headroom(goal_id, cap_usd=5.0) == 2.0


async def test_headroom_never_goes_negative() -> None:
    book = SpendBook(store=None)
    goal_id = new_task_id(FakeClock())
    await book.record(goal_id, 10.0)

    assert book.headroom(goal_id, cap_usd=5.0) == 0.0


async def test_restore_rebuilds_every_goals_running_total() -> None:
    store = InMemoryLedgerStore()
    goal_id = new_task_id(FakeClock())
    original = SpendBook(store)
    await original.record(goal_id, 2.0)

    restored = SpendBook(store)
    await restored.restore()

    assert restored.for_goal(goal_id) == 2.0


async def test_restore_is_a_no_op_with_no_store() -> None:
    book = SpendBook(store=None)

    await book.restore()  # Must not raise.

    assert book.for_goal(new_task_id(FakeClock())) == 0.0
