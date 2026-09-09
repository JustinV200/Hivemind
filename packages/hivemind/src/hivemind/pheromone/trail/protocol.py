"""Define PheromoneTrail, the append-only audit log protocol, and its query and segment models.

The Pheromone Trail is the Hive's append-only audit log (codingrules section 12, ADR-0007): every
state-changing action anywhere in the Hive writes a PheromoneEvent (hivemind.pheromone.events)
before the action counts as complete. This module defines the shape every trail store must offer
-- record one event, query the log, and move a slice of it (a segment) between processes -- as a
Protocol, so hivemind.pheromone.trail.memory (an in-process fake) and hivemind.pheromone.trail.
sqlite (the durable store) are interchangeable to everything above them. A segment is one node's
(one Queen or Warden process, ADR-0005) slice of the trail: a Warden that is offline keeps
recording to its own segment and merges it into the Queen's trail on reconnection, which is why
merge_segment must be safe to call twice with the same segment.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Implemented by hivemind.pheromone.trail.
    memory and hivemind.pheromone.trail.sqlite; used by every layer above Layer 1 that records or
    reads trail events, and by hivemind.pheromone.trail.tail and hivemind.pheromone.retention.
    Calls into hivemind.pheromone.events and waggle only.

Key invariants:
    - Every query() and export_segment() result is ordered by TRAIL_ORDER_KEY -- (at, node_id)
      -- ascending with ties kept in the order they were recorded, or fully reversed end-to-end
      when TrailQuery.newest_first is set; no implementation may return a different order.
    - record() raises DuplicateEventError (hivemind.pheromone.errors) on an id the store already
      holds; it never silently overwrites.
    - merge_segment() never raises on a duplicate id: it inserts every event whose id is unknown
      and ignores the rest, so merging the same segment twice is safe and returns 0 the second
      time.
    - A TrailSegment's every event has node_id equal to the segment's own node_id (validated); a
      segment always round-trips through model_dump_json()/model_validate_json() with every
      event's real subclass preserved, never downgraded to the PheromoneEvent base.

See Also:
    - docs/adr/0007-pheromone-trail-append-only-transactional-and-segmented.md for the decision
      this module implements.
    - .claude/codingrules.md section 12 for the Pheromone Trail rules, and section 8.1 for the
      Protocol-at-every-seam rule this module follows.
    - hivemind.pheromone.trail.memory and hivemind.pheromone.trail.sqlite for the two
      implementations.
    - hivemind.pheromone.events for PheromoneEvent and parse_event, which TrailSegment's events
      validator uses to rebuild the right subclass from JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)

from hivemind.pheromone.events import PheromoneEvent, parse_event
from waggle.ids import NodeId
from waggle.messages.base import NodeIdField, UtcDatetime

MAX_QUERY_LIMIT = 10_000  # A page this large is still a sub-second SQLite scan on an indexed table.
DEFAULT_QUERY_LIMIT = 1_000  # Generous for a human `hive trail tail`, small enough to stay fast.

# The Pheromone Trail's canonical order. Every query() and export_segment() result is sorted by
# exactly these two event attributes, in this order (ascending, or fully reversed when
# TrailQuery.newest_first is set), and two events that tie on both keep the order they were
# recorded in: a stable sort in hivemind.pheromone.trail.memory, `rowid` in
# hivemind.pheromone.trail.sqlite, and a merged segment is inserted in its exporter's order so the
# tie survives the merge. The event id is deliberately NOT the tiebreaker: a system clock can
# stamp several events with the same millisecond, and the ULID tails minted inside one
# millisecond are random, so ordering by id would shuffle an audit log's `blocked` before its
# `started`. hivemind.pheromone.trail.memory builds operator.attrgetter(*TRAIL_ORDER_KEY) from
# this tuple; hivemind.pheromone.trail.sqlite's literal `ORDER BY at, node_id, rowid` clause names
# the same columns plus the insertion tiebreaker (test_sqlite.py checks the two never drift apart).
TRAIL_ORDER_KEY: tuple[str, str] = ("at", "node_id")

__all__ = [
    "DEFAULT_QUERY_LIMIT",
    "MAX_QUERY_LIMIT",
    "TRAIL_ORDER_KEY",
    "PheromoneTrail",
    "TrailQuery",
    "TrailSegment",
]


class TrailQuery(BaseModel):
    """A filtered, ordered, bounded read of the Pheromone Trail.

    Every field is optional; an unset field applies no filter. `since`/`until` bound `at`
    inclusively on both ends, so a query with `since == until` matches events recorded at exactly
    that instant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    since: UtcDatetime | None = Field(
        default=None, description="Only events recorded at or after this time (inclusive)."
    )
    until: UtcDatetime | None = Field(
        default=None, description="Only events recorded at or before this time (inclusive)."
    )
    family: str | None = Field(
        default=None, description="Only events whose `kind` family segment equals this."
    )
    kind: str | None = Field(default=None, description="Only events whose `kind` equals this.")
    subject_id: str | None = Field(
        default=None, description="Only events whose `subject_id` equals this."
    )
    node_id: NodeIdField | None = Field(
        default=None, description="Only events recorded on this node's segment."
    )
    limit: int = Field(
        default=DEFAULT_QUERY_LIMIT,
        ge=1,
        le=MAX_QUERY_LIMIT,
        description="Maximum events to return; the store truncates rather than raising.",
    )
    newest_first: bool = Field(
        default=False,
        description="Reverse trail order end-to-end (newest event first) when true.",
    )


class TrailSegment(BaseModel):
    """One node's slice of the Pheromone Trail: an export time and that node's events.

    Produced by `PheromoneTrail.export_segment` and consumed by `PheromoneTrail.merge_segment`;
    the unit a Warden's offline trail travels as (a file, or over Waggle) to rejoin the Queen's
    trail on reconnection (codingrules section 12, ADR-0007).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: NodeIdField = Field(
        description="The node every event in this segment was recorded on."
    )
    exported_at: UtcDatetime = Field(description="When this segment was produced, from the Clock.")
    events: tuple[SerializeAsAny[PheromoneEvent], ...] = Field(
        description=(
            "This node's events, in trail order. SerializeAsAny keeps each event's own subclass "
            "on dump; the before-validator below rebuilds the right subclass on load."
        )
    )

    @field_validator("events", mode="before")
    @classmethod
    def _parse_events(cls, value: object) -> object:
        """Rebuild each plain-mapping event (as JSON decodes it) through parse_event.

        A value already built from Python objects (an existing PheromoneEvent instance) is left
        untouched; only a raw mapping -- what `model_validate_json` hands this validator for each
        array element -- needs decoding, so a segment round-trips with every event's real
        subclass, never the bare PheromoneEvent base.

        Args:
            value: The raw `events` field value, before pydantic's own type coercion.

        Returns:
            `value` unchanged if it is not a sequence; otherwise a tuple with every mapping
            element replaced by the PheromoneEvent subclass `parse_event` builds for it.
        """
        if not isinstance(value, list | tuple):
            return value  # not a sequence at all; let pydantic's own type check report that
        return tuple(parse_event(item) if isinstance(item, Mapping) else item for item in value)

    @model_validator(mode="after")
    def _events_belong_to_this_node(self) -> TrailSegment:
        """Reject a segment carrying an event recorded on a different node.

        Returns:
            `self`, unchanged, when every event's `node_id` matches the segment's own.

        Raises:
            ValueError: At least one event's `node_id` differs from `self.node_id`.
        """
        foreign = [event.id for event in self.events if event.node_id != self.node_id]
        if foreign:
            raise ValueError(
                f"segment for node {self.node_id!r} carries events from another node: {foreign}"
            )
        return self


class PheromoneTrail(Protocol):
    """Record, query and move segments of the Pheromone Trail.

    Implementations (`hivemind.pheromone.trail.memory.MemoryPheromoneTrail`,
    `hivemind.pheromone.trail.sqlite.SqlitePheromoneTrail`) must be safe to call concurrently:
    the Queen
    and every Warden write to their own trail store from more than one coroutine.
    """

    async def record(self, event: PheromoneEvent) -> None:
        """Append `event` to the trail.

        Args:
            event: The event to record. Its `id` must not already be on this trail.

        Returns:
            None, once the event is durably recorded.

        Raises:
            DuplicateEventError: `event.id` is already on this trail.
        """
        ...

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Return the events matching `query`, in trail order.

        Args:
            query: The filters, ordering and limit to apply.

        Returns:
            At most `query.limit` events satisfying every set field of `query`, ordered by
            TRAIL_ORDER_KEY ascending, or fully reversed when `query.newest_first` is true.
        """
        ...

    async def export_segment(self, node_id: NodeId, since: datetime | None = None) -> TrailSegment:
        """Export `node_id`'s events at or after `since`, as a TrailSegment.

        Args:
            node_id: The node whose events to export.
            since: Only include events recorded at or after this time; `None` exports from the
                node's beginning.

        Returns:
            A TrailSegment carrying `node_id`'s matching events, in trail order.
        """
        ...

    async def merge_segment(self, segment: TrailSegment) -> int:
        """Insert every event in `segment` whose id is not already on this trail.

        Idempotent: merging the same segment twice inserts nothing the second time, which is what
        lets a flaky reconnection retry a merge safely.

        Args:
            segment: The segment to merge in, typically from another node's `export_segment`.

        Returns:
            How many events were actually inserted; events whose id was already known are
            silently skipped and not counted.

        Raises:
            None on a duplicate id: unlike `record`, a known id here is expected, not an error.
        """
        ...
