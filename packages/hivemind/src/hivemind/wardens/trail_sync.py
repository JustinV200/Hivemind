"""Ship a Warden's own local Pheromone Trail segment to the Queen as TrailSegmentSync chunks.

Codingrules section 12: "The trail is one logical log made of per-node segments. A Warden that is
offline writes to its local segment; on reconnection the segment merges into the Queen's trail."
For a Warden on the Hive Stand that sentence costs nothing -- it records straight into the Queen's
own SQLite file -- but a Warden inside a Virtual Cell (ADR-0027) writes to a store that lives and
dies with the container, so unless the segment crosses the machine boundary every `warden.*`,
`cell.*`, `task.*` and `llm.*` event the Cell ever recorded is lost when the Undertaker destroys it.
`WaggleTrailSync` is that crossing: it exports this node's segment
(`hivemind.pheromone.PheromoneTrail.export_segment`), serialises it once as JSON, and sends it over
this Warden's own Queen link as `waggle.messages.swarm.TrailSegmentSync` chunks -- the exact message
the Swarm family already defines for a colonized device's own offline segment, reused unchanged
because a Virtual Cell's Warden is in precisely the same position. `TrailSync` is the Protocol
`hivemind.wardens.deps.WardenDeps.trail_sync` is typed against, so the Hive Stand's own composition
root leaves it `None` (its Warden and its Queen already share one trail store, and syncing would
merge a store into itself) and `hivemind.cli.in_cell.deps` wires a real one in. `_send` guards each
wire send with `hivemind.wardens.links.send_guarded` (phase 7 handoff, open item 8), so a Queen
link that closed or dropped mid-export ends the export, never the Warden's tick loop.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Called
    by `hivemind.wardens.warden.Warden` on its own heartbeat cadence and once more from `stop()`.
    Calls into `hivemind.pheromone` (PheromoneTrail, TrailSegment), `hivemind.wardens.links`
    (send_guarded) and waggle (clock, envelope, ids, messages, transport) only.

Key invariants:
    - Nothing is sent for an empty segment: `TrailSegmentSync.event_count` is bounded `>= 1`
      (`waggle.messages.swarm.colonized.MIN_EVENT_COUNT`), so a Cell that has recorded nothing yet
      has no valid frame to send in the first place.
    - `sync()` is a no-op when the segment has not grown since the last successful send (the last
      event's id is unchanged), so the Warden's own heartbeat cadence never re-ships an unchanged
      segment over and over.
    - Exactly one chunk carries `final=True` and the `sha256` of the whole export
      (`TrailSegmentSync`'s own validator), and every chunk of one export carries the same
      `first_event_id`/`last_event_id`/`total_bytes`, which is the group key the receiver
      (`hivemind.queen.trail.sync`) reassembles on.
    - A re-sent export is harmless: `PheromoneTrail.merge_segment` is idempotent by event id, so a
      retry after a half-delivered export inserts each event at most once; `sync()`/`_send()` never
      let `TransportClosedError`/`ConnectionLostError` escape (phase-7 handoff open item 8) and
      never advance `_last_event_id`/`_since` on a send that did not fully land, so a link that
      closed partway resends the whole export next time rather than leaving a gap in it.

See Also:
    - .claude/codingrules.md section 12 for the per-node-segments rule this module implements.
    - .claude/phase-7-handoff.md section 8 open item 8 for the guarded-send fix `_send` implements.
    - docs/waggle/spec.md section 8.10 for TrailSegmentSync's normative fields and bounds.
    - hivemind.queen.trail.sync for the Queen-side receiver that reassembles and merges these.
    - hivemind.pheromone.trail.protocol for TrailSegment, the unit this module serialises.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hivemind.pheromone import PheromoneTrail, TrailSegment
from hivemind.wardens.links import send_guarded
from waggle.clock import Clock
from waggle.envelope import Hop, wrap
from waggle.ids import CellId, EventId, HiveId, NodeId, WardenId
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.base import Transport

# The `segment_format_version` every chunk this module sends carries, and the only one
# `hivemind.queen.trail.sync` accepts: version 1 is `TrailSegment.model_dump_json()`'s own layout
# (`hivemind.pheromone.trail.protocol.TrailSegment`), UTF-8 encoded. Bump this, on both sides
# together, if that model's serialised shape ever changes incompatibly.
SEGMENT_FORMAT_VERSION = 1

__all__ = ["SEGMENT_FORMAT_VERSION", "TrailSync", "TrailSyncDeps", "WaggleTrailSync"]


@dataclass(frozen=True, slots=True)
class TrailSyncDeps:
    """Everything one WaggleTrailSync is built with (codingrules section 5.1).

    Attributes:
        trail: This node's own local Pheromone Trail; only its `export_segment` is read.
        transport: This Warden's own Queen link, the same one `WardenDeps.queen_link` carries.
        node_id: The node whose segment is exported and shipped; the Queen merges under this id.
        cell_id: The Cell this Warden owns, named on every chunk.
        warden_id: This Warden's own id; the `sender` of every envelope, and named on every chunk.
        hive_id: The Queen's own bee address; the `recipient` of every envelope this sender sends.
        clock: Injected time source for every envelope id and `sent_at` timestamp.
    """

    trail: PheromoneTrail
    transport: Transport
    node_id: NodeId
    cell_id: CellId
    warden_id: WardenId
    hive_id: HiveId
    clock: Clock


class TrailSync(Protocol):
    """Ship whatever this node's trail segment has gained since the last successful sync."""

    async def sync(self) -> bool:
        """Export this node's segment and send it, or do nothing when it has not grown.

        Never raises `TransportClosedError`/`ConnectionLostError` (phase-7 handoff open item 8):
        a closed or dropped Queen link is reported through the return value, not an exception,
        so this can never be what ends a Warden's tick loop.

        Returns:
            True once fully shipped, or when there was nothing new to ship (an empty or
            unchanged segment is the normal case on most ticks, not an error). False when the
            Queen link had already closed or dropped and shipping was interrupted; the segment
            is resent whole on the next call that succeeds.
        """
        ...


@dataclass(frozen=True, slots=True)
class _ExportMeta:
    """One export's own whole-segment fields, computed once and stamped on every chunk of it.

    Bundled (codingrules section 5.1: "introduce a frozen dataclass for the argument group")
    because `_chunk` builds one `TrailSegmentSync` per loop of `_send`, and every field below is
    the same across every chunk of one export -- only `chunk`/`offset`/`final` vary per call.
    """

    node_id: str
    from_at: datetime
    to_at: datetime
    first_event_id: str
    last_event_id: str
    event_count: int
    total_bytes: int
    sha256: str


class WaggleTrailSync:
    """Export this node's trail segment and send it to the Queen as TrailSegmentSync chunks.

    Owns its own mutable state in place (codingrules section 8.5): `_last_event_id` and `_since`
    advance with every successful send, and are what make a repeated `sync()` on an unchanged
    segment a no-op.
    """

    def __init__(self, deps: TrailSyncDeps) -> None:
        """Build a WaggleTrailSync; call `sync()` whenever the segment may have grown.

        Args:
            deps: Every collaborator this sender needs.
        """
        self._deps = deps
        self._last_event_id: EventId | None = None
        self._since: datetime | None = None

    async def sync(self) -> bool:
        """Export this node's segment and send it, unless it is empty or has not grown."""
        segment = await self._deps.trail.export_segment(self._deps.node_id, since=self._since)
        if not segment.events:
            return True  # Nothing recorded yet (or nothing since the last send): nothing to ship.
        last_event_id = EventId(segment.events[-1].id)
        if last_event_id == self._last_event_id:
            # `export_segment`'s `since` is inclusive and a FakeClock (or a busy millisecond) can
            # give several events the same timestamp, so the boundary event comes back every time;
            # this is what keeps the heartbeat cadence from re-shipping an unchanged segment.
            return True
        if not await self._send(segment):
            # Interrupted partway (or before the first chunk): _last_event_id/_since stay put,
            # so the next successful sync() resends this whole export rather than a gap in it.
            return False
        self._last_event_id = last_event_id
        self._since = segment.events[-1].at
        return True

    async def _send(self, segment: TrailSegment) -> bool:
        """Serialise `segment` once and send every chunk of it, `final` on the last.

        Returns:
            True once every chunk was handed to the link; False the moment one is not, logged by
            `send_guarded` and left there -- the caller never advances past a partial export.
        """
        body = segment.model_dump_json().encode("utf-8")
        meta = _ExportMeta(
            node_id=segment.node_id,
            from_at=segment.events[0].at,
            to_at=segment.events[-1].at,
            first_event_id=segment.events[0].id,
            last_event_id=segment.events[-1].id,
            event_count=len(segment.events),
            total_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        hop = Hop(
            sender=self._deps.warden_id,
            recipient=self._deps.hive_id,
            node_id=self._deps.node_id,
        )
        for offset in range(0, len(body), MAX_CHUNK_BYTES):
            chunk = body[offset : offset + MAX_CHUNK_BYTES]
            message = self._chunk(
                meta, chunk=chunk, offset=offset, final=offset + len(chunk) >= len(body)
            )
            envelope = wrap(message, hop, clock=self._deps.clock)
            if not await send_guarded(self._deps.transport, envelope):
                return False
        return True

    def _chunk(
        self, meta: _ExportMeta, *, chunk: bytes, offset: int, final: bool
    ) -> TrailSegmentSync:
        """Build one TrailSegmentSync frame describing the whole export and carrying one slice."""
        return TrailSegmentSync(
            node_id=meta.node_id,
            cell_id=self._deps.cell_id,
            warden_id=self._deps.warden_id,
            from_at=meta.from_at,
            to_at=meta.to_at,
            first_event_id=meta.first_event_id,
            last_event_id=meta.last_event_id,
            event_count=meta.event_count,
            segment_format_version=SEGMENT_FORMAT_VERSION,
            chunk=chunk,
            offset=offset,
            total_bytes=meta.total_bytes,
            final=final,
            # TrailSegmentSync's own validator: the digest belongs on the closing chunk and
            # nowhere else, because only there does it describe a complete export.
            sha256=meta.sha256 if final else None,
        )
