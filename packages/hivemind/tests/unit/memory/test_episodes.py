"""Tests for hivemind.memory.episodes: EpisodeRecord, record_episode, and EpisodeStream's pub/sub.

Fits into the Hive:
    Mirrors src/hivemind/memory/episodes.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.episodes for the module under test.
    - packages/hivemind/tests/unit/pheromone/events/test_families.py for the white-box-assertion
      pattern this module's cancellation test follows.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import cast

from builders.memory import make_episode

from hivemind.cell import HoneyClearance
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.episodes import EpisodeRecord, EpisodeStream, record_episode
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


@dataclass(frozen=True, slots=True)
class _ContextAndTrail:
    """A MemoryContext plus the trail its store records events on, for assertions."""

    ctx: MemoryContext
    trail: PheromoneTrail


def _make_context(clock: FakeClock) -> _ContextAndTrail:
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    ctx = MemoryContext(store=InMemoryMemoryStore(trail), identity=identity, clock=clock)
    return _ContextAndTrail(ctx=ctx, trail=trail)


def test_episode_record_round_trips_through_json() -> None:
    episode = make_episode()

    restored = EpisodeRecord.model_validate_json(episode.model_dump_json())

    assert restored == episode


async def test_record_episode_stores_it_and_records_memory_episode_on_the_trail() -> None:
    clock = FakeClock()
    context_and_trail = _make_context(clock)
    episode = make_episode(clock=clock)

    await record_episode(episode, context_and_trail.ctx)

    stored = await context_and_trail.ctx.store.list_episodes(None, HoneyClearance.C2, 10)
    assert stored == (episode,)
    events = await context_and_trail.trail.query(TrailQuery(subject_id=episode.id))
    assert len(events) == 1
    assert events[0].kind == "memory.episode"


async def test_episode_stream_yields_published_records_in_order() -> None:
    stream = EpisodeStream()
    subscription = stream.subscribe()
    received: list[EpisodeRecord] = []

    async def _consume() -> None:
        async for record in subscription:
            received.append(record)
            if len(received) == 2:
                break

    consumer = asyncio.ensure_future(_consume())
    await asyncio.sleep(0)  # Let the subscriber register before anything is published.
    first = make_episode()
    second = make_episode()
    await stream.publish(first)
    await stream.publish(second)
    await consumer

    assert [record.id for record in received] == [first.id, second.id]


async def test_episode_stream_drops_the_oldest_record_when_a_subscriber_is_slow() -> None:
    stream = EpisodeStream(queue_size=2)
    subscription = stream.subscribe()
    # Prime the subscriber: run subscribe() up to its first suspension (awaiting queue.get()),
    # registering it as a slow, not-yet-consuming subscriber.
    pending = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)

    first = make_episode()
    second = make_episode()
    third = make_episode()
    # None of these three awaits yields control back to the event loop (an uncontended
    # asyncio.Lock never suspends), so the primed subscriber cannot drain between them: the
    # third publish overflows the size-2 queue and evicts `first`.
    await stream.publish(first)
    await stream.publish(second)
    await stream.publish(third)

    received = await pending

    assert received.id == second.id  # first was dropped; the subscriber never saw it.
    # `subscribe()` is declared as an AsyncIterator (the Protocol-shaped view every caller
    # sees) but is an async generator underneath; the cast is what lets the test close it.
    await cast("AsyncGenerator[EpisodeRecord, None]", subscription).aclose()


async def test_episode_stream_unregisters_its_queue_when_the_subscriber_is_cancelled() -> None:
    stream = EpisodeStream()
    subscription = stream.subscribe()
    task = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)
    assert len(stream._subscribers) == 1  # white-box: confirms registration before cancelling.

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(stream._subscribers) == 0
