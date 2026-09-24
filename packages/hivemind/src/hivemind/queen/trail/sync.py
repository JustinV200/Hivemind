"""Reassemble TrailSegmentSync chunks from a remote Warden; merge the segment, or veil it.

The Queen-side half of `hivemind.wardens.trail_sync` (codingrules section 12: "The trail is one
logical log made of per-node segments. A Warden that is offline writes to its local segment; on
reconnection the segment merges into the Queen's trail"). A Warden inside a Virtual Cell (ADR-0027)
records into a store that dies with the container, so it ships its segment over its own Queen link
as `waggle.messages.swarm.TrailSegmentSync` chunks; `TrailSegmentReceiver` is what turns those back
into a `hivemind.pheromone.TrailSegment` and merges it, keyed by the sending node id.

`hivemind.queen.cell_gate.listener.CellListener` hands this every `swarm.trail_segment_sync` its
connections carry (bound by the composition root, `hivemind.cli.compose.virtual_cells`). The
contract this module offers is one call: hand it every `TrailSegmentSync` that arrives, in any
order of interleaving between senders, and it merges each complete export exactly once.

A Night Veil Cell's segment is never merged into the trail (codingrules section 12: its
execution records live in an ephemeral segment keyed to the Cell). Given the Night Veil boundary's
`EphemeralSegments`, `receive` offers every complete export to it first, keyed by the Cell the
chunk names: a Night Veil Cell's segment waits there, whole, until the Cell's teardown purges it,
and a segment arriving after that purge is dropped. Every other Cell's segment merges into the
trail exactly as before.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen`. Calls into
    `hivemind.pheromone` (PheromoneTrail, TrailSegment, EphemeralSegments) and waggle (ids,
    messages) only; it touches no Queen state, so the listener that drains it needs nothing else.

Key invariants:
    - A segment is merged exactly once per export: the `final` chunk triggers the merge and the
      partial buffer is dropped in the same call, whether the merge succeeded or was refused.
    - A chunk whose `segment_format_version` this module does not know is refused outright
      (`UnknownSegmentFormatError`), never merged on a guess -- that is the field's whole purpose
      (`waggle.messages.swarm.colonized.TrailSegmentSync.segment_format_version`).
    - A reassembled export whose SHA-256 does not match the `final` chunk's own `sha256`, or whose
      length does not match `total_bytes`, is refused (`CorruptSegmentError`): a partial merge of a
      truncated segment would put un-auditable gaps into the Hive's audit record.
    - Merging is idempotent by event id (`PheromoneTrail.merge_segment`), so a sender that
      re-ships an export after a dropped connection inserts nothing the second time.
    - A Night Veil Cell's segment never reaches the trail: it goes to the ephemeral segments, or
      nowhere once the Cell is purged.
    - `receive` never raises for an ordinary out-of-order or duplicate chunk; it only raises the
      two typed errors above, both of which mean "this export is not mergeable at all".

See Also:
    - .claude/codingrules.md section 12 for the per-node-segments rule and its merge semantics.
    - docs/waggle/spec.md section 8.10 for TrailSegmentSync's normative fields and bounds.
    - hivemind.wardens.trail_sync for WaggleTrailSync, the sender whose chunks this reassembles.
    - hivemind.pheromone.trail.protocol for TrailSegment and merge_segment's own contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from pydantic import ValidationError

from hivemind.pheromone import EphemeralSegments, PheromoneTrail, TrailSegment
from hivemind.wardens.trail_sync import SEGMENT_FORMAT_VERSION
from waggle.ids import CellId, NodeId
from waggle.messages.swarm import TrailSegmentSync

__all__ = [
    "CorruptSegmentError",
    "SegmentSyncError",
    "TrailSegmentReceiver",
    "UnknownSegmentFormatError",
]


class SegmentSyncError(Exception):
    """A TrailSegmentSync export this Queen cannot merge (codingrules section 10: typed errors)."""


class UnknownSegmentFormatError(SegmentSyncError):
    """A chunk declared a `segment_format_version` this Queen does not know how to read."""

    def __init__(self, version: int) -> None:
        """Name the version that was offered and the one this Queen understands."""
        super().__init__(
            f"TrailSegmentSync declared segment_format_version {version}; this Hive reads "
            f"version {SEGMENT_FORMAT_VERSION} only."
        )
        self.version = version


class CorruptSegmentError(SegmentSyncError):
    """A reassembled export did not match its own declared size, digest or event count."""

    def __init__(self, node_id: NodeId, detail: str) -> None:
        """Name the node whose export was refused and why."""
        super().__init__(f"TrailSegmentSync from node {node_id} is not mergeable: {detail}")
        self.node_id = node_id


@dataclass(slots=True)
class _Partial:
    """One export being reassembled: the bytes seen so far, held until its `final` chunk arrives.

    Owns its own mutable state in place (codingrules section 8.5): `body` grows as chunks arrive.
    Keyed in `TrailSegmentReceiver` by the group key the spec's section 5 chunking rule defines
    (node, first and last event id, total size), so two exports from the same node -- a retry and a
    later, longer segment -- never bleed into one another's buffer.
    """

    total_bytes: int
    body: bytearray = field(default_factory=bytearray)

    def add(self, offset: int, chunk: bytes) -> None:
        """Write `chunk` at `offset`, growing the buffer to `total_bytes` the first time."""
        if not self.body:
            self.body = bytearray(self.total_bytes)
        end = offset + len(chunk)
        if end > self.total_bytes:
            return  # A chunk claiming to run past the declared size; the digest check refuses it.
        self.body[offset:end] = chunk


# The group key one export's chunks share (docs/waggle/spec.md section 5): the sending node, the
# segment's own first and last event, and the total size. A sender that re-exports after recording
# more events produces a different last_event_id, hence a different key and a fresh buffer.
_GroupKey = tuple[str, str, str, int]


class TrailSegmentReceiver:
    """Collect one remote node's TrailSegmentSync chunks and merge each complete export once.

    Owns its own mutable state in place (codingrules section 8.5): `_partials` holds one entry per
    export currently being reassembled, and empties again as each one's `final` chunk arrives.
    """

    def __init__(self, trail: PheromoneTrail, night_veil: EphemeralSegments | None = None) -> None:
        """Build a receiver that merges into `trail`, veiling each Night Veil Cell's segment.

        Args:
            trail: The Queen's own Pheromone Trail; every accepted segment no Night Veil Cell owns
                is merged into it under the sending node's own id, so nodes never collide.
            night_veil: The Night Veil boundary's ephemeral segments (module docstring); None
                merges every segment into `trail`, for a Hive with no Virtual side.
        """
        self._trail = trail
        self._night_veil = night_veil
        self._partials: dict[_GroupKey, _Partial] = {}

    async def receive(self, message: TrailSegmentSync) -> int:
        """Take one chunk; merge and return the inserted count once the export is complete.

        Args:
            message: One `swarm.trail_segment_sync` payload, already decoded and verified by the
                transport's own Codec. The caller is responsible for the receiver rule the spec
                states -- `message.node_id` must equal the envelope's `node_id` and
                `message.warden_id` its `sender` -- because only the caller holds the envelope.

        Returns:
            The number of events actually inserted, once `message.final` completed an export (into
            the trail, or into a Night Veil Cell's ephemeral segment); `0` for every non-final
            chunk (nothing has been merged yet), for a re-merged export whose events are already
            held, and for a Night Veil Cell's segment arriving after its purge.

        Raises:
            UnknownSegmentFormatError: `message.segment_format_version` is not one this Hive reads.
            CorruptSegmentError: The reassembled export's size or SHA-256 digest does not match
                what the final chunk declared, it is not a readable `TrailSegment`, or it does not
                hold the `event_count` events the chunk promised.
        """
        if message.segment_format_version != SEGMENT_FORMAT_VERSION:
            raise UnknownSegmentFormatError(message.segment_format_version)
        key = _group_key(message)
        partial = self._partials.setdefault(key, _Partial(total_bytes=message.total_bytes))
        partial.add(message.offset, message.chunk)
        if not message.final:
            return 0
        # Dropped before the merge runs, not after: a refused export must not leave a half-filled
        # buffer behind for the sender's own retry to append to (invariant 1).
        del self._partials[key]
        return await self._merge(message, bytes(partial.body))

    async def _merge(self, message: TrailSegmentSync, body: bytes) -> int:
        """Verify the reassembled `body` against `message`'s own claims, then merge it."""
        node_id = NodeId(message.node_id)
        # `sha256` is never None on a final chunk (TrailSegmentSync's own validator), and this is
        # only ever reached for a final chunk; the check below is what actually proves the bytes.
        if len(body) != message.total_bytes or hashlib.sha256(body).hexdigest() != message.sha256:
            raise CorruptSegmentError(
                node_id, f"the reassembled {len(body)} bytes do not match its declared digest"
            )
        try:
            segment = TrailSegment.model_validate_json(body)
        except ValidationError as exc:
            raise CorruptSegmentError(node_id, f"it is not a readable TrailSegment: {exc}") from exc
        if len(segment.events) != message.event_count:
            raise CorruptSegmentError(
                node_id,
                f"it holds {len(segment.events)} events, not the {message.event_count} promised",
            )
        # A Night Veil Cell's segment waits in its ephemeral segment instead (module docstring);
        # None means the boundary owns no such Cell, so the segment merges as it always has.
        if self._night_veil is not None:
            veiled = await self._night_veil.merge(CellId(message.cell_id), segment)
            if veiled is not None:
                return veiled
        # TrailSegment's own validator already refused a segment carrying another node's events,
        # so merging is keyed by node id by construction: nothing here re-checks it.
        return await self._trail.merge_segment(segment)


def _group_key(message: TrailSegmentSync) -> _GroupKey:
    """Build the key every chunk of one export shares (see `_GroupKey`'s own comment)."""
    return (
        message.node_id,
        message.first_event_id,
        message.last_event_id,
        message.total_bytes,
    )
