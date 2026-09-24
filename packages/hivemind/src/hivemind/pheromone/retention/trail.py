"""Define VeiledTrail: the Queen's trail, with the Night Veil boundary applied to every record.

Every Queen-side component records through one `PheromoneTrail` (the lifecycle, the dispatcher,
the Forage ledger's writers, the attestation, the Guard's Enforcer, the Undertaker), and many of
their records name a Night Veil Cell or one of its tasks: its provisioning edges, its Warden's
arrival, its placement, grant and hosting plan, its destruction. Codingrules section 12 lets only
the lifecycle skeleton of such a Cell outlive it, so rather than asking every writer to know the
rule, the composition root hands them this decorator in place of the durable trail. `record` asks
`EphemeralSegments` which Night Veil Cell (if any) an event is about; an event about none goes to
the durable trail untouched, exactly as before, and an event about one sends only its skeleton copy
(`hivemind.pheromone.retention.skeleton.skeleton_event`, when its kind has one) to the durable
trail and the whole event to the Cell's ephemeral segment while the Cell lives. Reads, exports and
merges pass straight through: nothing veiled is ever on the durable trail to be read.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.retention`.
    Built by `hivemind.cli.compose.night_veil` over the Hive's durable trail and handed to every
    Queen-side writer in its place. Implements `hivemind.pheromone.trail.PheromoneTrail`. Calls
    into `hivemind.pheromone.retention.segments`, `...retention.skeleton`, `hivemind.pheromone.
    events` and `hivemind.pheromone.trail` only.

Key invariants:
    - An event about no Night Veil Cell or task reaches the durable trail unchanged: a MEADOW or
      PROPOLIS Cell's records are exactly what they were without this decorator.
    - An event about one reaches the durable trail only as its skeleton copy, and not at all when
      its kind has none; its whole form lives in memory only, in the Cell's segment.
    - The skeleton copy is recorded before the whole event is kept, so a durable-trail failure
      (a duplicate id) leaves nothing half-routed in the segment.

See Also:
    - .claude/codingrules.md section 12 for the boundary this decorator applies.
    - hivemind.pheromone.retention.segments for EphemeralSegments, the routing index and store.
    - hivemind.pheromone.retention.skeleton for the skeleton cut itself.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.pheromone.events import PheromoneEvent
from hivemind.pheromone.retention.segments import EphemeralSegments
from hivemind.pheromone.retention.skeleton import skeleton_event
from hivemind.pheromone.trail.protocol import PheromoneTrail, TrailQuery, TrailSegment
from waggle.ids import NodeId

__all__ = ["VeiledTrail"]


class VeiledTrail:
    """A PheromoneTrail that routes each Night Veil record to its Cell's segment, skeleton aside."""

    def __init__(self, durable: PheromoneTrail, segments: EphemeralSegments) -> None:
        """Wrap the durable trail with the Night Veil boundary.

        Args:
            durable: The Hive's own trail; every read and every unveiled write goes here.
            segments: Decides which Cell a record is about, and holds that Cell's whole records.
        """
        self._durable = durable
        self._segments = segments

    @property
    def durable(self) -> PheromoneTrail:
        """The wrapped durable trail, for a writer that must bypass the boundary (the purge)."""
        return self._durable

    async def record(self, event: PheromoneEvent) -> None:
        """Record `event`, veiled when it is about a Night Veil Cell or task; see the module doc."""
        veiling = self._segments.veiling(event)
        if veiling is None:
            await self._durable.record(event)
            return
        # The skeleton copy first (module docstring): a refused durable write stops here.
        skeleton = skeleton_event(event)
        if skeleton is not None:
            await self._durable.record(skeleton)
        await self._segments.keep(veiling, event)

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Read the durable trail; see `PheromoneTrail.query`."""
        return await self._durable.query(query)

    async def export_segment(self, node_id: NodeId, since: datetime | None = None) -> TrailSegment:
        """Export one node's durable segment; see `PheromoneTrail.export_segment`."""
        return await self._durable.export_segment(node_id, since)

    async def merge_segment(self, segment: TrailSegment) -> int:
        """Merge a shipped segment into the durable trail; see `PheromoneTrail.merge_segment`.

        A Night Veil Cell's segment never arrives here: `hivemind.queen.trail.sync` hands it to
        `EphemeralSegments.merge` first, and only a segment no Night Veil Cell owns reaches this.
        """
        return await self._durable.merge_segment(segment)
