"""Provide the Night Veil boundary: the package's one removal path, teardown-time only.

Codingrules section 12: a Night Veil Cell (a `CombShieldLevel.NIGHT_VEIL` Virtual Cell, always
routed over Tor and a VPN, coding rules 8.7) never keeps its execution records after teardown.
Everything that Cell's own Warden node wrote to the trail lives in an ephemeral segment keyed to
that node id, and the VPN gateway's and Tor daemons' own per-Cell connection and circuit logs are
a parallel side channel with the exact same no-retention rule -- purging only the trail and
leaving those logs behind would be a side channel that defeats the whole guarantee. This module
purges both, in one call, and is the *only* place `hivemind.pheromone` ever removes a row:
`hivemind.pheromone.trail.sqlite` contains no `UPDATE` or `DELETE` token at all (decision 7), so
every other module in this package only ever inserts.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by the Undertaker (the cleanup
    Worker, `hivemind.hive.lifecycle`) at Night Veil teardown, once the Cell's task has ended and
    its Warden has stopped writing. Calls into hivemind.common.sqlite, hivemind.pheromone.events,
    hivemind.pheromone.trail.memory and hivemind.pheromone.trail.protocol.

Key invariants:
    - `SqliteSegmentPurge.purge_segment` runs the package's only `DELETE` statement, in one
      transaction, under its own `asyncio.Lock`.
    - `NightVeilTeardownPurge.purge` always purges the trail segment before any side channel, and
      records the `cell.purged` skeleton event last, so a failure partway through never reports a
      purge that did not actually happen.
    - The `cell.purged` event's payload carries only counts and the segment's node id -- never any
      of the purged content itself (codingrules section 12).

See Also:
    - .claude/codingrules.md section 12 for the Night Veil boundary this module implements.
    - docs/adr/0007-pheromone-trail-append-only-transactional-and-segmented.md for the decision
      that this is the package's one deletion path.
    - hivemind.pheromone.trail.protocol for PheromoneTrail, and hivemind.pheromone.trail.sqlite
      for the table every other module in this package only ever inserts into.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.common.sqlite import transaction
from hivemind.pheromone.events import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, NodeId, new_event_id
from waggle.messages.base import CellIdField, NodeIdField

# The package's one removal statement (decision 7); every other module here only ever inserts.
_PURGE_SEGMENT_SQL = "DELETE FROM pheromone_events WHERE node_id = ?"
# The one kind this module ever records; already in CellEvent.KINDS (hivemind.pheromone.events).
_PURGED_KIND = "cell.purged"

__all__ = [
    "MemorySegmentPurge",
    "NightVeilTeardownPurge",
    "PurgeReport",
    "SegmentPurge",
    "SideChannelPurger",
    "SqliteSegmentPurge",
    "TrailRecorder",
]


class SegmentPurge(Protocol):
    """Remove one node's rows from a Pheromone Trail store."""

    async def purge_segment(self, node_id: NodeId) -> int:
        """Remove every event recorded under `node_id`.

        Args:
            node_id: The node whose segment to remove.

        Returns:
            How many events were removed. Purging a node with nothing recorded, or purging the
            same node twice, returns 0 without error.
        """
        ...


class SqliteSegmentPurge:
    """SegmentPurge over a SqlitePheromoneTrail's own connection: the trail's one DELETE path."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open connection whose schema already has `pheromone_events`.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
        """
        self._connection = connection
        # Serialises purge_segment on this instance the same way SqlitePheromoneTrail's own lock
        # serialises its four methods: asyncio.to_thread may run each call on a different worker
        # thread, and this connection must never have two transactions open on it at once.
        self._lock = asyncio.Lock()

    async def purge_segment(self, node_id: NodeId) -> int:
        """Remove `node_id`'s rows; see `SegmentPurge.purge_segment` for the full contract."""
        async with self._lock:
            # Blocking: one DELETE in one transaction, matched on the indexed node_id column, so
            # even a large ephemeral Night Veil segment removes in one fast pass.
            return await asyncio.to_thread(_purge_transaction, self._connection, node_id)


class MemorySegmentPurge:
    """SegmentPurge over a MemoryPheromoneTrail, for tests and demo paths."""

    def __init__(self, trail: MemoryPheromoneTrail) -> None:
        """Wrap the in-memory trail to purge.

        Args:
            trail: The trail whose `drop_segment` this delegates to.
        """
        self._trail = trail

    async def purge_segment(self, node_id: NodeId) -> int:
        """Remove `node_id`'s rows; see `SegmentPurge.purge_segment` for the full contract."""
        # drop_segment is synchronous (a plain list rebuild, no I/O); see its own docstring for
        # why it needs no lock at teardown time.
        return self._trail.drop_segment(node_id)


class SideChannelPurger(Protocol):
    """Purge one Cell's records from a log the Pheromone Trail does not own.

    The two known implementers arrive in later phases: the VPN gateway's per-Cell connection log
    (phase 5) and the Tor daemon's per-Cell circuit log, including the Hive Stand's hidden-service
    log for that Cell (phase 11). Both exist because codingrules section 12 lists them by name as
    part of the Night Veil boundary: purging the trail alone would leave exactly the side channel
    the no-retention guarantee is meant to close.
    """

    async def purge(self, cell_id: CellId) -> int:
        """Remove every record this side channel holds for `cell_id`.

        Args:
            cell_id: The Cell whose records to remove.

        Returns:
            How many records were removed.
        """
        ...


class PurgeReport(BaseModel):
    """What a Night Veil teardown purge removed: counts only, never the removed content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: CellIdField = Field(description="The Night Veil Cell this purge was run for.")
    segment_node_id: NodeIdField = Field(description="The trail segment (node id) that was purged.")
    events_purged: int = Field(ge=0, description="Trail events removed from the segment.")
    side_channel_records_purged: int = Field(
        ge=0, description="Records removed across every registered SideChannelPurger, summed."
    )


@dataclass(frozen=True, slots=True)
class TrailRecorder:
    """Group the Queen-side trail writer identity NightVeilTeardownPurge needs to record with.

    Exists only so `NightVeilTeardownPurge.__init__` stays within codingrules 5.1's five-parameter
    limit: `trail`, `clock`, `hive_id` and `node_id` always travel together as one writer identity.
    """

    trail: PheromoneTrail  # Where the skeleton event lands; never the purged segment itself.
    clock: Clock  # Timestamps the recorded event.
    hive_id: HiveId  # The Hive the recorded event belongs to.
    node_id: NodeId  # The Queen's own node id, not the purged Cell's Warden's node id.


class NightVeilTeardownPurge:
    """Purge a Night Veil Cell's trail segment and side channels, then record that it happened."""

    def __init__(
        self,
        segments: SegmentPurge,
        side_channels: Sequence[SideChannelPurger],
        recorder: TrailRecorder,
    ) -> None:
        """Wire the purge's three collaborators.

        Args:
            segments: Removes the Cell's ephemeral trail segment.
            side_channels: Every registered side-channel purger, run in this order.
            recorder: The Queen-side trail writer identity for the surviving skeleton event.
        """
        self._segments = segments
        self._side_channels = side_channels
        self._recorder = recorder

    async def purge(self, cell_id: CellId, segment_node_id: NodeId, actor: str) -> PurgeReport:
        """Purge the Cell's trail segment and every side channel, then record the skeleton event.

        Args:
            cell_id: The Night Veil Cell being torn down.
            segment_node_id: The node id of that Cell's Warden's ephemeral trail segment.
            actor: Who initiated the teardown (a bee id, or "human"/"system"); becomes the
                recorded event's `actor`.

        Returns:
            A PurgeReport with the counts of what was removed.
        """
        # Step 1: the trail segment itself, first, so a later side-channel failure never leaves
        # the trail's own ephemeral rows as the one part of the boundary left unpurged.
        events_purged = await self._segments.purge_segment(segment_node_id)

        # Step 2: every registered side channel, in the order the composition root wired them
        # (typically the VPN gateway before the Tor daemon); the no-retention guarantee holds only
        # if every one of these actually ran.
        side_channel_total = 0
        for side_channel in self._side_channels:
            side_channel_total += await side_channel.purge(cell_id)

        report = PurgeReport(
            cell_id=cell_id,
            segment_node_id=segment_node_id,
            events_purged=events_purged,
            side_channel_records_purged=side_channel_total,
        )
        # Step 3: the one lifecycle record the Night Veil boundary keeps -- what was purged and
        # how much, on the Queen's own trail (never the purged node's, which is now gone).
        await self._recorder.trail.record(_purge_event(self._recorder, cell_id, actor, report))
        return report


def _purge_transaction(connection: sqlite3.Connection, node_id: NodeId) -> int:
    """Remove every row for `node_id`, inside its own transaction; the package's one DELETE path."""
    with transaction(connection):
        cursor = connection.execute(_PURGE_SEGMENT_SQL, (node_id,))
        return cursor.rowcount


def _purge_event(
    recorder: TrailRecorder, cell_id: CellId, actor: str, report: PurgeReport
) -> CellEvent:
    """Build the `cell.purged` skeleton event a completed purge records on the Queen's trail."""
    return CellEvent(
        id=new_event_id(recorder.clock),
        hive_id=recorder.hive_id,
        node_id=recorder.node_id,
        at=recorder.clock.now(),
        actor=actor,
        kind=_PURGED_KIND,
        subject_id=cell_id,
        payload={
            "segment_node_id": report.segment_node_id,
            "events_purged": report.events_purged,
            "side_channel_records_purged": report.side_channel_records_purged,
        },
    )
