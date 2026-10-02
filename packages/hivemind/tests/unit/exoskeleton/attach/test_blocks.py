"""Unit tests for hivemind.exoskeleton.attach.blocks: what the browser blocked, on the trail."""

from __future__ import annotations

from builders.cells import make_identity

from hivemind.exoskeleton.attach.blocks import BLOCKED_KIND, MAX_BLOCK_EVENTS, BlockTrail
from hivemind.exoskeleton.attach.handle import ExoskeletonTrail
from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock
from hivemind.pheromone import DuplicateEventError, MemoryPheromoneTrail
from hivemind.pheromone.events import PheromoneEvent
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id


def _sink(trail: MemoryPheromoneTrail, clock: FakeClock) -> BlockTrail:
    identity = make_identity(clock=clock)
    return BlockTrail(ExoskeletonTrail(trail, identity, clock, new_cell_id(clock)))


async def _payloads(trail: MemoryPheromoneTrail) -> list[dict[str, object]]:
    events = await trail.query(TrailQuery())
    assert all(event.kind == BLOCKED_KIND for event in events)
    return [dict(event.payload) for event in events]


async def test_each_distinct_block_is_one_event_with_its_rule_and_host() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    sink = _sink(trail, clock)

    await sink(BrowserBlock(BlockRule.NET, "tracker.example"))
    await sink(BrowserBlock(BlockRule.NET, "tracker.example"))  # A page retrying: one event.
    await sink(BrowserBlock(BlockRule.FILE))
    await sink(BrowserBlock(BlockRule.DEVTOOLS))

    assert await _payloads(trail) == [
        {"rule": "net", "host": "tracker.example"},
        {"rule": "file"},
        {"rule": "devtools"},
    ]


async def test_a_page_cannot_flood_the_trail() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    sink = _sink(trail, clock)

    for index in range(MAX_BLOCK_EVENTS + 10):
        await sink(BrowserBlock(BlockRule.NET, f"h{index}.example"))

    assert len(await _payloads(trail)) == MAX_BLOCK_EVENTS


class _RefusingTrail(MemoryPheromoneTrail):
    """A trail that refuses every append, as a corrupted or full one might."""

    async def record(self, event: PheromoneEvent) -> None:
        raise DuplicateEventError(event.id)


async def test_a_trail_that_refuses_the_event_never_breaks_the_block() -> None:
    clock = FakeClock()
    sink = _sink(_RefusingTrail(clock), clock)

    await sink(BrowserBlock(BlockRule.NET, "tracker.example"))  # Does not raise.
