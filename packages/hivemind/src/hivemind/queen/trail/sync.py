"""Reassemble TrailSegmentSync chunks from a remote Warden; merge the segment, or veil it.

The Queen-side half of `hivemind.wardens.trail_sync` (codingrules section 12: "The trail is one
logical log made of per-node segments. A Warden that is offline writes to its local segment; on
reconnection the segment merges into the Queen's trail"). A Warden inside a Virtual Cell (ADR-0027)
records into a store that dies with the container, so it ships its segment over its own Queen link
as `waggle.messages.swarm.TrailSegmentSync` chunks; `TrailSegmentReceiver` is what turns those back
into a `hivemind.pheromone.TrailSegment` and merges it, keyed by the sending node id.

The gates (`hivemind.queen.cell_gate` for Virtual Cells' Wardens, `hivemind.queen.swarm_gate` for
devices') drain each remote Warden's link and hand this module every chunk that names the node and
Warden its link proved (the spec's receiver rule, which only the holder of the envelope can
check); what either refuses, the link records as `guard.segment_refused` (roadmap step 10.6). The
contract this module offers is one call: hand it every `TrailSegmentSync` that arrives, in any
order of interleaving between senders, and it merges each complete export exactly once.

From phase 11 (roadmap 11.9, ADR-0053) a complete export is also *authenticated* before it is
merged: given the `SegmentKeys` of its gate, the receiver checks the final chunk's
`segment_signature` against the key registered for the sending node (a Swarm node's is required, a
Virtual Cell's is verified when present). ADR-0007's dedupe proves order, not authorship; this is
what stops a hijacked connection or a faulty node putting events into the audit record under
another node's name.

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
    - An export whose sender must sign (`SegmentKey.is_required`) and did not, or whose signature
      does not verify, is refused before anything is merged (`UnsignedSegmentError`,
      `BadSegmentSignatureError`), and its partial buffer is dropped like any refused export's.
    - A reassembled export whose SHA-256 does not match the `final` chunk's own `sha256`, or whose
      length does not match `total_bytes`, is refused (`CorruptSegmentError`): a partial merge of a
      truncated segment would put un-auditable gaps into the Hive's audit record. So is a segment
      of any node but the chunk's own (`ForeignSegmentError`): only the chunk's node is proved by
      its link.
    - Merging is idempotent by event id (`PheromoneTrail.merge_segment`), so a sender that
      re-ships an export after a dropped connection inserts nothing the second time.
    - A Night Veil Cell's segment never reaches the trail: it goes to the ephemeral segments, or
      nowhere once the Cell is purged.
    - `receive` never raises for an ordinary out-of-order or duplicate chunk; it only raises the
      typed errors above, each of which means "this export is not mergeable at all".

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
from hivemind.queen.trail.errors import (
    BadSegmentSignatureError,
    CorruptSegmentError,
    ForeignSegmentError,
    SegmentSyncError,
    UnknownSegmentFormatError,
    UnsignedSegmentError,
)
from hivemind.queen.trail.signature import SegmentKeys, check_segment_signature
from hivemind.wardens.trail_sync import SEGMENT_FORMAT_VERSION
from waggle.ids import CellId, NodeId
from waggle.messages.swarm import TrailSegmentSync

# The typed errors live in `.errors` (so `.signature` can raise them without importing this module)
# and are re-exported here, where importers have always found them.
__all__ = [
    "BadSegmentSignatureError",
    "CorruptSegmentError",
    "ForeignSegmentError",
    "SegmentSyncError",
    "TrailSegmentReceiver",
    "UnknownSegmentFormatError",
    "UnsignedSegmentError",
]


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

    def __init__(
        self,
        trail: PheromoneTrail,
        night_veil: EphemeralSegments | None = None,
        keys: SegmentKeys | None = None,
    ) -> None:
        """Build a receiver that merges into `trail`, veiling each Night Veil Cell's segment.

        Args:
            trail: The Queen's own Pheromone Trail; every accepted segment no Night Veil Cell owns
                is merged into it under the sending node's own id, so nodes never collide.
            night_veil: The Night Veil boundary's ephemeral segments (module docstring); None
                merges every segment into `trail`, for a Hive with no Virtual side.
            keys: Where the key a sender signs with is found (module docstring); None checks no
                signature, for a receiver whose gate holds no node keys.
        """
        self._trail = trail
        self._night_veil = night_veil
        self._keys = keys
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
            UnsignedSegmentError: The sender's segments must be signed (a Swarm node's) and the
                final chunk carries no signature.
            BadSegmentSignatureError: The final chunk's signature is not the sender's.
            CorruptSegmentError: The reassembled export's size or SHA-256 digest does not match
                what the final chunk declared, it is not a readable `TrailSegment`, it does not
                hold the `event_count` events the chunk promised; `ForeignSegmentError`, the
                subclass, when it is another node's segment.
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
        # Authenticity before integrity: the signature covers the digest the final chunk declares,
        # so a forged export is refused as forged, not as merely corrupt.
        await self._authenticate(message)
        return await self._merge(message, bytes(partial.body))

    async def _authenticate(self, message: TrailSegmentSync) -> None:
        """Check the final chunk's signature against its sender's key, if this receiver has keys."""
        if self._keys is None:
            return  # A gate with no node keys checks no signature.
        check_segment_signature(message, await self._keys.key_for(message))

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
        # The chunk's node is the one its link proved (the caller's receiver rule); a segment of
        # another node inside it would merge events that node never shipped (roadmap step 10.6).
        if segment.node_id != node_id:
            raise ForeignSegmentError(node_id, f"it is node {segment.node_id}'s segment")
        # A Night Veil Cell's segment waits in its ephemeral segment instead (module docstring);
        # None means the boundary owns no such Cell, so the segment merges as it always has.
        if self._night_veil is not None:
            veiled = await self._night_veil.merge(CellId(message.cell_id), segment)
            if veiled is not None:
                return veiled
        # TrailSegment's own validator already refused a segment carrying another node's events,
        # so every event merged is the chunk's own node's.
        return await self._trail.merge_segment(segment)


def _group_key(message: TrailSegmentSync) -> _GroupKey:
    """Build the key every chunk of one export shares (see `_GroupKey`'s own comment)."""
    return (
        message.node_id,
        message.first_event_id,
        message.last_event_id,
        message.total_bytes,
    )
