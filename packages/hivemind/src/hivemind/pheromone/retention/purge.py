"""Define NightVeilTeardownPurge: the Night Veil purge, the package's one removal path.

Codingrules section 12: a Night Veil Cell never keeps its execution records after teardown. While
the Cell lives its records sit in an ephemeral segment on the Queen's side
(`hivemind.pheromone.retention.segments.EphemeralSegments`); at teardown this purge takes that
segment whole, folds its `capping.*` detail into one `capping.summary` per tier (the one Capping
record the skeleton keeps), removes any row a Cell's own node ever left on the durable trail (a
segment merged before the Cell was known to be Night Veil, or by a Queen older than this
boundary), clears every registered side channel, and records one `cell.purged` event carrying
counts only. The side channels are the logs the trail does not own whose no-retention rule is the
same: `SideChannels` names each one, and the ones no store exists for yet are named seams.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.retention`.
    Called by `hivemind.hive.night_veil.boundary` on every path a Night Veil Cell ends: a
    lifecycle teardown (a finished task, a failed provision, a Hive shutdown), an Absconding, and
    a Queen restart that finds the Cell gone. Calls into `hivemind.common.sqlite`,
    `hivemind.pheromone.events`, `hivemind.pheromone.retention.segments`, `...retention.skeleton`
    and `hivemind.pheromone.trail` only.

Key invariants:
    - `SqliteSegmentPurge.purge_segment` runs the package's only `DELETE` statement, in one
      transaction, under its own `asyncio.Lock`; `hivemind.pheromone.trail.sqlite` has none.
    - The purge takes the ephemeral segment first and records `cell.purged` last, so a failure
      partway through never reports a purge that did not happen, and never leaves the segment
      behind in memory either.
    - The Queen's own node is never purged: only a node the Cell's segment came from, or one
      whose durable rows are about the Cell, and never the recorder's.
    - `capping.summary` and `cell.purged` carry counts and a tier name only, never the content of
      anything purged.

See Also:
    - .claude/codingrules.md section 12 for the Night Veil boundary this module ends.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the design.
    - docs/adr/0007-pheromone-trail-append-only-transactional-and-segmented.md for why this is the
      package's one deletion path.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.common.sqlite import ConnectionThread, connect, transaction
from hivemind.pheromone.events import CappingEvent, CellEvent, PheromoneEvent
from hivemind.pheromone.retention.segments import EphemeralSegments, TakenSegment
from hivemind.pheromone.retention.skeleton import SUMMARY_KIND, tier_counts
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import MAX_QUERY_LIMIT, PheromoneTrail, TrailQuery
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, NodeId, new_event_id
from waggle.messages.base import CellIdField

# The package's one removal statement (decision 7); every other module here only ever inserts.
_PURGE_SEGMENT_SQL = "DELETE FROM pheromone_events WHERE node_id = ?"
# The one purge record kind; already in CellEvent.KINDS (hivemind.pheromone.events).
_PURGED_KIND = "cell.purged"
# The most a side channel may take to clear one Cell's records: a gateway's API or a store's own
# delete, not a scan of the Hive; past it the purge fails loudly rather than wait for ever.
SIDE_CHANNEL_TIMEOUT_S = 30.0

__all__ = [
    "SIDE_CHANNEL_TIMEOUT_S",
    "LazySqliteSegmentPurge",
    "MemorySegmentPurge",
    "NightVeilTeardownPurge",
    "PurgeReport",
    "SegmentPurge",
    "SideChannelPurger",
    "SideChannels",
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
    """SegmentPurge over a connection to the Hive's SQLite file: the trail's one DELETE path."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open connection whose schema already has `pheromone_events`.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`, normally its own
                to the Hive's file (ADR-0006: separate connections to one file share it under WAL).
        """
        self._connection = connection
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled
        # await can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-purge")
        # Serialises purge_segment on this instance the same way SqlitePheromoneTrail's own lock
        # serialises its four methods: one whole purge at a time, never two interleaved.
        self._lock = asyncio.Lock()

    async def purge_segment(self, node_id: NodeId) -> int:
        """Remove `node_id`'s rows; see `SegmentPurge.purge_segment` for the full contract."""
        async with self._lock:
            # Blocking: one DELETE in one transaction, matched on the indexed node_id column, so
            # even a large Night Veil segment removes in one fast pass.
            return await self._thread.run(_purge_transaction, self._connection, node_id)


class LazySqliteSegmentPurge:
    """SqliteSegmentPurge over its own connection to the Hive's file, opened at the first purge.

    Most processes that build the Night Veil boundary never purge (an offline `hive cells`
    command, a Hive that runs no Night Veil work), so none of them opens a connection it never
    uses; the first purge opens one, off the event loop, and every later purge reuses it.
    """

    def __init__(self, database: Path) -> None:
        """Remember the Hive's SQLite file; nothing is opened yet.

        Args:
            database: The file whose `pheromone_events` table the purge deletes from.
        """
        self._database = database
        self._purge: SqliteSegmentPurge | None = None
        # Two first purges racing would each open a connection; one opens it, the other waits.
        self._lock = asyncio.Lock()

    async def purge_segment(self, node_id: NodeId) -> int:
        """Remove `node_id`'s rows; see `SegmentPurge.purge_segment` for the full contract."""
        async with self._lock:
            if self._purge is None:
                # Blocking: sqlite3.connect opens the file and applies the store's pragmas.
                connection = await asyncio.to_thread(connect, self._database)
                self._purge = SqliteSegmentPurge(connection)
        return await self._purge.purge_segment(node_id)


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
    """Purge one Cell's records from a log the Pheromone Trail does not own."""

    async def purge(self, cell_id: CellId) -> int:
        """Remove every record this side channel holds for `cell_id`.

        Args:
            cell_id: The Cell whose records to remove.

        Returns:
            How many records were removed.
        """
        ...


@dataclass(frozen=True, slots=True)
class SideChannels:
    """Every store beyond the trail a Night Veil teardown must clear, each by its own name.

    A field left None is a registered seam: the store it names does not exist in this Hive yet,
    so nothing there holds a record of the Cell, and the purger that store ships with plugs in
    here by name. Every store a Night Veil Cell writes itself (its trail segment, memory, Tor and
    OpenVPN logs, scratch) lives inside the Cell and dies with its backend resources, which the
    backend's own destroy removes (the `hivemind.hive` README says what that must cover).

    Attributes:
        vpn_gateway: A VPN gateway outside the Cell, with its own per-Cell connection log. None
            exists: the Night Veil image runs its own OpenVPN client, whose log dies with it.
        tor_hidden_service: The Hive Stand's own hidden-service log for the Cell (phase 11,
            when the Hive runs its hidden service instead of the operator).
        nectar: The raw findings the Cell's bees deposit (phase 7, the Honey Store).
        honey: Honey ripened from them (phase 7), except what the work deposited on purpose at
            C0 or C1, labelled `origin_tier = NIGHT_VEIL`, which is kept (codingrules 12).
    """

    vpn_gateway: SideChannelPurger | None = None
    tor_hidden_service: SideChannelPurger | None = None
    nectar: SideChannelPurger | None = None
    honey: SideChannelPurger | None = None

    def registered(self) -> tuple[SideChannelPurger, ...]:
        """Return every side channel that exists, in the fixed order the purge runs them."""
        channels = (self.vpn_gateway, self.tor_hidden_service, self.nectar, self.honey)
        return tuple(channel for channel in channels if channel is not None)


class PurgeReport(BaseModel):
    """What a Night Veil teardown purge removed: counts only, never the removed content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: CellIdField = Field(description="The Night Veil Cell this purge was run for.")
    events_purged: int = Field(
        ge=0, description="Events removed: the ephemeral segment's plus any durable trail rows."
    )
    side_channel_records_purged: int = Field(
        ge=0, description="Records removed across every registered SideChannelPurger, summed."
    )
    summaries: int = Field(ge=0, description="`capping.summary` events recorded, one per tier.")


@dataclass(frozen=True, slots=True)
class TrailRecorder:
    """Group the Queen-side trail writer identity a Night Veil record is written with.

    Exists so constructors stay within codingrules 5.1's five-parameter limit: `trail`, `clock`,
    `hive_id` and `node_id` always travel together as one writer identity.
    """

    trail: PheromoneTrail  # Where the record lands; the durable trail for the purge's own.
    clock: Clock  # Timestamps the recorded event.
    hive_id: HiveId  # The Hive the recorded event belongs to.
    node_id: NodeId  # The Queen's own node id, never a purged Cell's Warden's.


class NightVeilTeardownPurge:
    """Purge a Night Veil Cell's segment, trail rows and side channels, then record that it did."""

    def __init__(
        self,
        segments: SegmentPurge,
        side_channels: Sequence[SideChannelPurger],
        recorder: TrailRecorder,
        *,
        ephemeral: EphemeralSegments | None = None,
    ) -> None:
        """Wire the purge's collaborators.

        Args:
            segments: Removes a node's rows from the durable trail.
            side_channels: Every registered side-channel purger (`SideChannels.registered`), run
                in this order.
            recorder: The durable trail and the Queen's identity the summaries and `cell.purged`
                are written with, past the boundary rather than through it.
            ephemeral: The Queen's store of living Night Veil Cells' segments; None where there
                is none to take (a test of the durable half alone).
        """
        self._segments = segments
        self._side_channels = side_channels
        self._recorder = recorder
        self._ephemeral = ephemeral

    async def purge(
        self, cell_id: CellId, actor: str, *, segment_node_ids: Iterable[NodeId] = ()
    ) -> PurgeReport:
        """Purge everything `cell_id` left, keep its per-tier Capping counts, record the purge.

        Args:
            cell_id: The Night Veil Cell being torn down (or found gone).
            actor: Who ended it (a bee id, or "human"/"system"); every recorded event's `actor`.
            segment_node_ids: Further nodes of the Cell's own, beyond those its segment and the
                durable trail name, whose rows must go.

        Returns:
            A PurgeReport with the counts of what was removed and summarised.
        """
        # Step 1: take the ephemeral segment whole, before anything can fail, so no failure below
        # leaves the Cell's detail held in memory.
        taken = await self._take(cell_id)
        # Step 2: the one Capping record the skeleton keeps, folded from the detail just taken.
        counts = tier_counts(taken.events)
        for count in counts:
            await self._record(SUMMARY_KIND, cell_id, actor, count.payload())
        # Step 3: any row a node of the Cell's own ever left on the durable trail, before any
        # side channel, so a later side-channel failure never leaves the trail's rows behind.
        nodes = await self._cell_nodes(cell_id, taken, segment_node_ids)
        rows = sum([await self._segments.purge_segment(node) for node in sorted(nodes)])
        # Step 4: every registered side channel, in order; the guarantee holds only if all ran.
        side_total = 0
        for side_channel in self._side_channels:
            async with asyncio.timeout(SIDE_CHANNEL_TIMEOUT_S):
                side_total += await side_channel.purge(cell_id)
        report = PurgeReport(
            cell_id=cell_id,
            events_purged=len(taken.events) + rows,
            side_channel_records_purged=side_total,
            summaries=len(counts),
        )
        # Step 5: the purge's own record, last, carrying counts only.
        await self._record(_PURGED_KIND, cell_id, actor, _purged_payload(report))
        return report

    async def _take(self, cell_id: CellId) -> TakenSegment:
        """Take `cell_id`'s ephemeral segment, or an empty one where no store was wired."""
        if self._ephemeral is None:
            return TakenSegment(events=(), node_ids=frozenset())
        return await self._ephemeral.take(cell_id)

    async def _cell_nodes(
        self, cell_id: CellId, taken: TakenSegment, extra: Iterable[NodeId]
    ) -> set[NodeId]:
        """Return every node of the Cell's own that may have rows on the durable trail.

        Its shipped segments name some; the durable trail names the rest, since only a Cell's own
        Warden records events about the Cell under a node other than the Queen's. The Queen's own
        node is removed last, whatever named it: purging it would delete the Hive's whole trail.
        """
        nodes = {*taken.node_ids, *extra}
        query = TrailQuery(subject_id=cell_id, limit=MAX_QUERY_LIMIT)
        nodes.update(NodeId(event.node_id) for event in await self._recorder.trail.query(query))
        nodes.discard(self._recorder.node_id)
        return nodes

    async def _record(
        self, kind: str, cell_id: CellId, actor: str, payload: dict[str, JsonValue]
    ) -> None:
        """Record one purge-owned event about `cell_id` on the durable trail."""
        recorder = self._recorder
        family: type[PheromoneEvent] = CappingEvent if kind == SUMMARY_KIND else CellEvent
        event = family(
            id=new_event_id(recorder.clock),
            hive_id=recorder.hive_id,
            node_id=recorder.node_id,
            at=recorder.clock.now(),
            actor=actor,
            kind=kind,
            subject_id=cell_id,
            payload=payload,
        )
        await recorder.trail.record(event)


def _purge_transaction(connection: sqlite3.Connection, node_id: NodeId) -> int:
    """Remove every row for `node_id`, inside its own transaction; the package's one DELETE path."""
    with transaction(connection):
        cursor = connection.execute(_PURGE_SEGMENT_SQL, (node_id,))
        return cursor.rowcount


def _purged_payload(report: PurgeReport) -> dict[str, JsonValue]:
    """Build `cell.purged`'s payload: how much went, and nothing of what it was."""
    return {
        "events_purged": report.events_purged,
        "side_channel_records_purged": report.side_channel_records_purged,
    }
