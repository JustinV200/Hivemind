"""Tests for hivemind.queen.forage.ledger.seats: SeatBook.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/seats.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.seats for the module under test.
"""

from __future__ import annotations

from hivemind.queen.forage.ledger.seats import SeatBook
from hivemind.queen.forage.ledger.store_memory import InMemoryLedgerStore


async def test_free_is_none_before_capacity_is_ever_declared() -> None:
    book = SeatBook(store=None)

    assert book.free("src_1") is None
    assert book.in_use("src_1") == 0


async def test_free_is_total_minus_in_use() -> None:
    book = SeatBook(store=None)
    await book.set_capacity("src_1", 2)

    await book.mark_started("src_1")

    assert book.in_use("src_1") == 1
    assert book.free("src_1") == 1


async def test_mark_finished_never_drives_in_use_below_zero() -> None:
    book = SeatBook(store=None)
    await book.set_capacity("src_1", 2)

    await book.mark_finished("src_1")  # No matching mark_started.

    assert book.in_use("src_1") == 0


async def test_six_calls_against_a_two_seat_source_never_exceed_two_in_use() -> None:
    book = SeatBook(store=None)
    await book.set_capacity("src_1", 2)

    for _ in range(6):
        await book.mark_started("src_1")
        assert book.in_use("src_1") <= 2
        await book.mark_finished("src_1")

    assert book.in_use("src_1") == 0


async def test_total_capacity_sums_every_declared_source() -> None:
    book = SeatBook(store=None)
    await book.set_capacity("src_1", 2)
    await book.set_capacity("src_2", 3)

    assert book.total_capacity() == 5


async def test_restore_rebuilds_capacity_but_never_in_flight_usage() -> None:
    store = InMemoryLedgerStore()
    original = SeatBook(store)
    await original.set_capacity("src_1", 2)
    await original.mark_started("src_1")  # Simulates a call still running when the crash hits.

    restored = SeatBook(store)
    await restored.restore()

    assert restored.free("src_1") == 2  # Nothing genuinely in flight across a restart.
    assert restored.in_use("src_1") == 0


async def test_restore_is_a_no_op_with_no_store() -> None:
    book = SeatBook(store=None)

    await book.restore()  # Must not raise.

    assert book.total_capacity() == 0
