"""Read the Pheromone Trail a page at a time, from a cursor, filtered as the caller asks.

The trail route pages the Hive's audit log (ADR-0032) without an offset, which a live log would
shift under the reader. The trail's order is ``(at, node_id)`` with ties kept as recorded, and many
events share one instant, so a cursor that is only a time would skip or repeat them. A cursor here
is an instant and how many events at exactly that instant were already read (``TrailCursor``):
reading oldest first continues from ``since``, newest first continues back from ``until``, and the
first ``skip`` events at the cursor's own instant are the ones the previous page already had. The
store is asked for ``skip + limit`` events (both bounded, so the sum stays within the store's own
ceiling) and the already-read ones are dropped here.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads``. Called by
    ``hivemind.entrance.routes.hive.trail``. Calls into ``hivemind.pheromone`` and the trail's
    view models.

Key invariants:
    - A page never repeats an event of the previous one and never skips one, as long as no event
      is recorded later at an instant already paged past (a merged offline segment can be).
    - ``skip + limit`` never exceeds the store's ``MAX_QUERY_LIMIT``.

See Also:
    - hivemind.pheromone.trail.protocol for TRAIL_ORDER_KEY and TrailQuery.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hivemind.observation import (
    DEFAULT_TRAIL_PAGE,
    TrailCursor,
    TrailFilters,
    TrailPage,
    trail_event_view,
)
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery

__all__ = ["TrailRead", "read_trail"]


@dataclass(frozen=True, slots=True)
class TrailRead:
    """One page's request: the filters, the cursor and the page size.

    Attributes:
        family: Only events of this family (the kind before its dot).
        kind: Only events of this exact kind.
        subject_id: Only events about this subject.
        since: Oldest first: read from this instant (inclusive).
        until: Read up to this instant (inclusive); newest first: read back from it.
        skip: Events at exactly the cursor's instant the previous page already had.
        limit: Most events to answer; at most ``MAX_TRAIL_PAGE`` (``TrailFilters`` checks it).
        newest_first: Read newest first instead of in trail order.
    """

    family: str | None = None
    kind: str | None = None
    subject_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    skip: int = 0
    limit: int = DEFAULT_TRAIL_PAGE
    newest_first: bool = False

    @property
    def cursor_at(self) -> datetime | None:
        """The instant the page continues from: ``until`` newest first, else ``since``."""
        return self.until if self.newest_first else self.since

    @classmethod
    def of(cls, filters: TrailFilters) -> TrailRead:
        """Build a read from a validated query string.

        Args:
            filters: The query string the route validated.

        Returns:
            The read it asks for.
        """
        return cls(**filters.model_dump())


async def read_trail(trail: PheromoneTrail, request: TrailRead) -> TrailPage:
    """Read one page of the trail after the cursor, with the cursor for the next.

    Args:
        trail: The Pheromone Trail.
        request: The filters, the cursor and the page size.

    Returns:
        The page, and where the next one starts (null once the trail has no more).
    """
    query = TrailQuery(
        since=request.since,
        until=request.until,
        family=request.family,
        kind=request.kind,
        subject_id=request.subject_id,
        limit=request.skip + request.limit,
        newest_first=request.newest_first,
    )
    # Latency: one local indexed trail read, at most MAX_QUERY_LIMIT rows.
    events = await trail.query(query)
    page = _after_skip(events, request.cursor_at, request.skip)[: request.limit]
    return TrailPage(
        events=[trail_event_view(event) for event in page], next=_next_cursor(request, page)
    )


def _after_skip(
    events: Sequence[PheromoneEvent], cursor_at: datetime | None, skip: int
) -> list[PheromoneEvent]:
    """Drop the first ``skip`` events at exactly the cursor's instant: the last page had them."""
    kept: list[PheromoneEvent] = []
    skipped = 0
    # The cursor's instant is the query's inclusive bound, so its events come first in the page.
    for event in events:
        if skipped < skip and cursor_at is not None and event.at == cursor_at:
            skipped += 1
            continue
        kept.append(event)
    return kept


def _next_cursor(request: TrailRead, page: Sequence[PheromoneEvent]) -> TrailCursor | None:
    """Return where the next page starts; None when this page was not full."""
    if len(page) < request.limit:
        return None
    last_at = page[-1].at
    seen = sum(1 for event in page if event.at == last_at)
    # A page that never left the cursor's instant carries the earlier skip forward.
    if request.cursor_at is not None and last_at == request.cursor_at:
        seen += request.skip
    if request.newest_first:
        return TrailCursor(since=request.since, until=last_at, skip=seen)
    return TrailCursor(since=last_at, until=request.until, skip=seen)
