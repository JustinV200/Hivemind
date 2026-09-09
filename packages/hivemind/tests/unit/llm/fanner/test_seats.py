"""Tests for hivemind.llm.fanner.seats: SeatMeter.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/seats.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.seats for the module under test.
"""

from __future__ import annotations

import asyncio

from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.fanner.seats import SeatMeter
from waggle.clock import FakeClock


def _tempo(bar: AccuracyBar) -> Tempo:
    return Tempo(accuracy=bar)


async def test_acquire_returns_immediately_when_a_seat_is_free() -> None:
    meter = SeatMeter(capacity=1, clock=FakeClock())

    waited_s = await meter.acquire(_tempo(AccuracyBar.NORMAL))

    assert waited_s == 0.0
    assert meter.in_flight == 1
    assert meter.queued == 0


async def test_acquire_queues_a_second_caller_at_capacity_one() -> None:
    clock = FakeClock()
    meter = SeatMeter(capacity=1, clock=clock)
    await meter.acquire(_tempo(AccuracyBar.NORMAL))

    waiter = asyncio.ensure_future(meter.acquire(_tempo(AccuracyBar.NORMAL)))
    await asyncio.sleep(0)  # Let the waiter task run up to its own await point.

    assert meter.queued == 1
    assert meter.in_flight == 1
    await meter.release()
    await waiter


async def test_release_with_no_waiters_frees_the_seat_for_the_next_acquire() -> None:
    meter = SeatMeter(capacity=1, clock=FakeClock())
    await meter.acquire(_tempo(AccuracyBar.NORMAL))

    await meter.release()

    assert meter.in_flight == 0
    waited_s = await meter.acquire(_tempo(AccuracyBar.NORMAL))
    assert waited_s == 0.0


async def test_release_hands_the_seat_to_the_highest_priority_waiter_first() -> None:
    clock = FakeClock()
    meter = SeatMeter(capacity=1, clock=clock)
    await meter.acquire(_tempo(AccuracyBar.NORMAL))  # Takes the only seat.

    order: list[str] = []

    async def _wait_and_record(label: str, bar: AccuracyBar) -> None:
        await meter.acquire(_tempo(bar))
        order.append(label)

    # LOW enqueues first, then CRITICAL; CRITICAL must still be admitted first (roadmap 3.12a:
    # "a CRITICAL/HIGH-tempo caller is admitted before a LOW one").
    low_task = asyncio.ensure_future(_wait_and_record("low", AccuracyBar.LOW))
    await asyncio.sleep(0)
    critical_task = asyncio.ensure_future(_wait_and_record("critical", AccuracyBar.CRITICAL))
    await asyncio.sleep(0)
    assert meter.queued == 2

    await meter.release()  # Frees the seat for the highest-priority waiter: critical.
    await critical_task
    assert order == ["critical"]

    await meter.release()  # Frees the seat for the remaining waiter: low.
    await low_task
    assert order == ["critical", "low"]


async def test_release_breaks_ties_between_equal_tempo_waiters_by_arrival_order() -> None:
    clock = FakeClock()
    meter = SeatMeter(capacity=1, clock=clock)
    await meter.acquire(_tempo(AccuracyBar.NORMAL))

    order: list[str] = []

    async def _wait_and_record(label: str) -> None:
        await meter.acquire(_tempo(AccuracyBar.NORMAL))
        order.append(label)

    first_task = asyncio.ensure_future(_wait_and_record("first"))
    await asyncio.sleep(0)
    second_task = asyncio.ensure_future(_wait_and_record("second"))
    await asyncio.sleep(0)

    await meter.release()
    await first_task
    await meter.release()
    await second_task

    assert order == ["first", "second"]


async def test_acquire_measures_how_long_a_queued_caller_actually_waited() -> None:
    clock = FakeClock()
    meter = SeatMeter(capacity=1, clock=clock)
    await meter.acquire(_tempo(AccuracyBar.NORMAL))

    async def _wait_and_advance() -> float:
        return await meter.acquire(_tempo(AccuracyBar.NORMAL))

    waiter = asyncio.ensure_future(_wait_and_advance())
    await asyncio.sleep(0)
    clock.advance(2.5)
    await meter.release()

    waited_s = await waiter
    assert waited_s == 2.5
