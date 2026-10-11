"""Tests for hivemind.queen.forage.ledger.measured: MeasuredGradeBook.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/measured.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.measured for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.queen.forage.ledger import ForageLedger, InMemoryLedgerStore, MeasuredGradeBook
from waggle.clock import FakeClock


async def test_record_replaces_the_earlier_measurement_and_grades_reads_it() -> None:
    book = MeasuredGradeBook(None)
    now = FakeClock().now()

    await book.record("fast-local", 2, now, "first")
    await book.record("fast-local", 5, now, "second")

    assert book.grades() == {"fast-local": 5}
    assert [row.evidence for row in book.rows()] == ["second"]


async def test_record_refuses_a_grade_outside_one_to_five() -> None:
    book = MeasuredGradeBook(None)

    with pytest.raises(ValidationError):
        await book.record("fast-local", 0, FakeClock().now(), "bad")

    assert book.grades() == {}


async def test_record_writes_through_and_restore_reads_it_back() -> None:
    store = InMemoryLedgerStore()
    await MeasuredGradeBook(store).record("fast-local", 3, FakeClock().now(), "run")
    fresh = MeasuredGradeBook(store)

    await fresh.restore()

    assert fresh.grades() == {"fast-local": 3}


async def test_the_forage_ledger_restores_its_measured_grades_with_the_rest() -> None:
    store = InMemoryLedgerStore()
    await store.put_measured_grade("fast-local", 4, FakeClock().now(), "run")
    ledger = ForageLedger(store=store)

    await ledger.restore()

    assert ledger.measured.grades() == {"fast-local": 4}
