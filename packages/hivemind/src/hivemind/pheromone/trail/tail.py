"""Follow the Pheromone Trail live: yield its backlog, then every new event, forever.

`follow` is what powers `hive trail tail --follow` (a human watching the Hive act) and the
Observation Hive's live trail stream: a caller that wants to react to new events as they land,
rather than polling `PheromoneTrail.query` by hand and tracking its own high-water mark. It does
exactly that tracking once, correctly, so every caller shares the same "no duplicate, no missed
event" behaviour even when two events share the same `at` (two events minted in the same
millisecond are common: ids are ULIDs, not the ordering key).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by hivemind.cli.trail and
    hivemind.observation. Calls into hivemind.pheromone.trail.protocol and waggle.clock only.

Key invariants:
    - Never returns on its own: the only ways to stop it are cancelling the task that is
      iterating it or calling its `aclose()`. No code path here catches
      `asyncio.CancelledError` without re-raising, so cancellation always propagates.
    - Every event yielded from the trail's existing backlog, and from every subsequent poll, is
      yielded exactly once, in trail order, even across an `at` tie.

See Also:
    - hivemind.pheromone.trail.protocol for PheromoneTrail.query and TrailQuery, this module's
      only calls.
    - waggle.clock for the Clock protocol; `clock.sleep` is what makes this testable with
      FakeClock instead of a real timer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime

from hivemind.pheromone.events import PheromoneEvent
from hivemind.pheromone.trail.protocol import PheromoneTrail, TrailQuery
from waggle.clock import Clock

DEFAULT_POLL_INTERVAL_S = 1.0  # Frequent enough to feel live in a terminal, gentle enough to poll.

__all__ = ["DEFAULT_POLL_INTERVAL_S", "follow"]


async def follow(
    trail: PheromoneTrail,
    clock: Clock,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    since: datetime | None = None,
) -> AsyncIterator[PheromoneEvent]:
    """Yield every Pheromone Trail event from `since` onward, then keep polling forever.

    Args:
        trail: The trail to read from.
        clock: Injected clock; `clock.sleep` is how this waits between polls, so a test drives it
            with `waggle.clock.FakeClock` instead of a real timer.
        poll_interval_s: Seconds to sleep between polls once the backlog is exhausted.
        since: Only yield events recorded at or after this time; `None` starts from the trail's
            beginning.

    Yields:
        Every matching PheromoneEvent, oldest first, exactly once.
    """
    # The starting backlog: whatever the trail already holds at or after `since`, in trail order.
    backlog = await trail.query(TrailQuery(since=since))
    for event in backlog:
        yield event
    last_at, last_ids = _advance_watermark(backlog, since, set())

    while True:
        # External wait: how long between polls. Uncaught cancellation here (no `except
        # CancelledError`) is the one way this generator ever stops on its own.
        await clock.sleep(poll_interval_s)

        page = await trail.query(TrailQuery(since=last_at))
        for event in _new_events(page, last_at, last_ids):
            yield event
        last_at, last_ids = _advance_watermark(page, last_at, last_ids)


def _new_events(
    events: Sequence[PheromoneEvent], last_at: datetime | None, last_ids: set[str]
) -> Iterator[PheromoneEvent]:
    """Yield only the events not already yielded at the `last_at` high-water mark.

    `since` is inclusive, so a poll re-fetches every event already seen at exactly `last_at`;
    `last_ids` is what tells those apart from events newly written at that same instant.
    """
    for event in events:
        if last_at is not None and event.at == last_at and event.id in last_ids:
            continue  # already yielded on a previous pass; skip to avoid a duplicate
        yield event


def _advance_watermark(
    events: Sequence[PheromoneEvent], last_at: datetime | None, last_ids: set[str]
) -> tuple[datetime | None, set[str]]:
    """Return the (last_at, last_ids) high-water mark after processing `events`.

    An empty `events` changes nothing: the previous mark is still correct, since nothing new was
    seen. Otherwise the mark becomes the newest `at` in `events` and every id recorded at exactly
    that instant, which is exactly what the next poll needs to avoid re-yielding them.
    """
    if not events:
        return last_at, last_ids
    newest_at = events[-1].at
    return newest_at, {event.id for event in events if event.at == newest_at}
