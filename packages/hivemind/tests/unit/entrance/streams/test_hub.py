"""Test hivemind.entrance.streams.hub: one trail follower, bounded per-subscriber queues.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/hub.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.entrance import make_identity

from hivemind.entrance.streams import CloseReason, StreamClosedError, StreamHub
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent
from waggle.clock import SystemClock

_INVITED = "guard.entrance_invited"
_DENIED = "guard.entrance_denied"


async def _record(trail: MemoryPheromoneTrail, clock: SystemClock, kind: str) -> None:
    """Record one Entrance event of ``kind``."""
    identity = make_identity(clock)
    await trail.record(identity.event(clock, kind, identity.hive_id, {}))


async def test_every_subscriber_gets_the_events_its_filter_accepts() -> None:
    clock = SystemClock()
    trail = MemoryPheromoneTrail(clock)
    hub = StreamHub(trail, clock, poll_interval_s=0.01)
    invited = hub.subscribe("invites", lambda event: event.kind == _INVITED)
    everything = hub.subscribe("all", lambda event: True)
    since = clock.now()
    async with asyncio.TaskGroup() as group:
        runner = group.create_task(hub.run(since))
        await _record(trail, clock, _INVITED)
        await _record(trail, clock, _DENIED)

        async with asyncio.timeout(2.0):
            seen: list[PheromoneEvent] = []
            while len(seen) < 2:
                seen.extend(await everything.next_batch())
            only = await invited.next_batch()
        runner.cancel()

    assert [event.kind for event in seen] == [_INVITED, _DENIED]
    assert [event.kind for event in only] == [_INVITED]


def test_a_subscriber_past_its_backlog_is_closed_as_fallen_behind() -> None:
    clock = SystemClock()
    hub = StreamHub(MemoryPheromoneTrail(clock), clock)
    slow = hub.subscribe("slow", lambda event: True, backlog=1)
    identity = make_identity(clock)
    events = [identity.event(clock, _INVITED, identity.hive_id, {}) for _ in range(2)]

    for event in events:
        slow.offer(event)

    assert slow.closed is CloseReason.FELL_BEHIND
    assert hub.subscribers == 0


async def test_a_closed_subscription_raises_its_reason_once_drained() -> None:
    clock = SystemClock()
    hub = StreamHub(MemoryPheromoneTrail(clock), clock)
    subscription = hub.subscribe("view", lambda event: True)

    subscription.close(CloseReason.SESSION_ENDED)

    with pytest.raises(StreamClosedError) as caught:
        await subscription.next_batch()
    assert caught.value.reason is CloseReason.SESSION_ENDED


async def test_a_stopping_hub_closes_every_subscription() -> None:
    clock = SystemClock()
    hub = StreamHub(MemoryPheromoneTrail(clock), clock, poll_interval_s=0.01)
    subscription = hub.subscribe("view", lambda event: True)
    async with asyncio.TaskGroup() as group:
        runner = group.create_task(hub.run())
        await asyncio.sleep(0)
        runner.cancel()

    assert subscription.closed is CloseReason.SHUTTING_DOWN
