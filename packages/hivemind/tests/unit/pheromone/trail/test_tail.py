"""Tests for hivemind.pheromone.trail.tail.follow: live-tailing the Pheromone Trail.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/trail/tail.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.trail.tail for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

import pytest

from hivemind.pheromone.events import CellEvent, PheromoneEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import PheromoneTrail
from hivemind.pheromone.trail.tail import follow
from waggle.clock import FakeClock
from waggle.ids import NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id

_POLL_INTERVAL_S = 1.0  # Arbitrary; only its value passed to clock.advance() matters here.


def _make_cell_event(clock: FakeClock, node_id: NodeId) -> CellEvent:
    """Build a well-formed CellEvent recorded on `node_id`, timestamped at the clock's `now()`."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=node_id,
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=new_cell_id(clock),
        payload={},
    )


def _start(trail: PheromoneTrail, clock: FakeClock) -> AsyncGenerator[PheromoneEvent, None]:
    """Start follow() at the fixed poll interval.

    follow()'s documented contract returns `AsyncIterator[PheromoneEvent]`; the cast below only
    restores the narrower type it actually is at runtime (an async generator function), which is
    what lets these tests call `aclose()` on the result.
    """
    generator = follow(trail, clock, poll_interval_s=_POLL_INTERVAL_S)
    return cast("AsyncGenerator[PheromoneEvent, None]", generator)


async def _poll_for_next(
    generator: AsyncIterator[PheromoneEvent],
) -> asyncio.Task[PheromoneEvent]:
    """Start `anext(generator)` and run it up to its `await clock.sleep(...)` suspend point.

    Returns the still-pending task; the caller then records events and calls `clock.advance`
    before awaiting it, so the events land on the trail before the poll this task will run.
    """
    task = asyncio.ensure_future(anext(generator))
    await asyncio.sleep(0)  # one loop turn is enough for the task to reach clock.sleep and suspend
    return task


async def test_follow_yields_the_existing_backlog_first() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    node_id = new_node_id(clock)
    backlog_event = _make_cell_event(clock, node_id)
    await trail.record(backlog_event)
    generator = _start(trail, clock)

    yielded = await anext(generator)

    assert yielded == backlog_event
    await generator.aclose()


async def test_follow_yields_a_new_event_recorded_between_polls() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    node_id = new_node_id(clock)
    generator = _start(trail, clock)
    # Nothing on the trail yet: follow() drains an empty backlog and suspends on its first poll.
    task = await _poll_for_next(generator)

    new_event = _make_cell_event(clock, node_id)
    await trail.record(new_event)
    clock.advance(_POLL_INTERVAL_S)

    assert await task == new_event
    await generator.aclose()


async def test_follow_does_not_reyield_events_sharing_the_watermark_at() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    node_id = new_node_id(clock)
    generator = _start(trail, clock)
    task = await _poll_for_next(generator)

    # Two events minted back to back share the same `at`: FakeClock only moves on advance().
    tied_a = _make_cell_event(clock, node_id)
    tied_b = _make_cell_event(clock, node_id)
    await trail.record(tied_a)
    await trail.record(tied_b)
    clock.advance(_POLL_INTERVAL_S)
    first = await task
    # Still within the same poll's page, so the next yield needs no further sleep or advance.
    second = await anext(generator)

    assert {first.id, second.id} == {tied_a.id, tied_b.id}

    # A further poll at the same watermark `at` must not re-yield tied_a/tied_b, only a genuinely
    # new event recorded after them.
    task = await _poll_for_next(generator)
    later_event = _make_cell_event(clock, node_id)
    await trail.record(later_event)
    clock.advance(_POLL_INTERVAL_S)

    assert await task == later_event
    await generator.aclose()


async def test_follow_propagates_cancellation_instead_of_swallowing_it() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    generator = _start(trail, clock)
    task = await _poll_for_next(generator)  # suspended inside its pending clock.sleep

    # A real caller stops an endless follow() by cancelling the task iterating it; follow() has
    # no `except asyncio.CancelledError` anywhere, so this must propagate, not be swallowed.
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_follow_aclose_after_a_completed_yield_stops_it_cleanly() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    node_id = new_node_id(clock)
    await trail.record(_make_cell_event(clock, node_id))
    generator = _start(trail, clock)
    await anext(generator)  # drains the one backlog event

    await generator.aclose()  # returns promptly; never waits out poll_interval_s
