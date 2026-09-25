"""Record what a Virtual Cell's link refused: a frame that failed its signature, a segment unmerged.

Roadmap step 10.6 has the Guard Bee (the Hive's security watcher, reading the Queen's trail) watch
for node integrity failures, and two of them happen at the Cell gate, the Queen's listener every
Virtual Cell's Warden dials. A frame on an attached Cell's link whose signature fails
(`waggle.errors.SignatureError`) ends the link, as it always did, and is now recorded as
`guard.envelope_refused`. A `swarm.trail_segment_sync` chunk (one slice of the trail segment the
Cell's Warden ships) is handed to the receiver only when it names the node and Warden its link
proved, the receiver rule of docs/waggle/spec.md section 8.10, and the Cell its link proved too:
the receiver routes a complete export by the chunk's own `cell_id` (a Night Veil Cell's to its
ephemeral segment, codingrules section 12), so a chunk naming another Cell would ship a Night Veil
Cell's segment onto the durable trail under a MEADOW Cell's id, or bury a MEADOW Cell's in a Night
Veil Cell's segment. One that does not, and an export the receiver refuses (an unknown format,
bytes that do not match what it declared, or another node's segment), is recorded as
`guard.segment_refused`, and the link stays up: an honest Cell's bad export is a fault the Queen
should hear of, not a reason to cut its Warden off mid-task.

Both events are the Queen's own records, about the Cell whose own link carried the frame, and name
a reason, never the frame. A frame that fails before its link is attached proves no Cell (whoever
dialled may have named any Cell's id), so the listener closes that connection and records nothing
against anyone.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.cell_gate`.
    Built by `.listener` once per attached link. Calls into `hivemind.common.logging`,
    `hivemind.pheromone` (GuardEvent, TrailRecorder), `hivemind.queen.trail` (the receiver and
    its errors) and waggle (envelope, errors, ids, messages) only.

Key invariants:
    - A link records each kind and reason at most once, so no Cell can flood the trail by
      repeating a refused frame; the Guard Bee's rules fire on the first.
    - A chunk that breaks the receiver rule, or names a Cell its link did not prove, never
      reaches the receiver, so nothing it carries is merged or veiled.
    - Nothing is recorded without a recorder: a listener built without one refuses the same way.

See Also:
    - hivemind.queen.cell_gate.listener for the link this records about.
    - hivemind.queen.trail.sync for the receiver and its refusals.
    - hivemind/workers/roles/guard_bee/rules.toml for the rules that count these events.
"""

from __future__ import annotations

from enum import StrEnum

from hivemind.common.logging import get_logger
from hivemind.pheromone import GuardEvent, TrailRecorder
from hivemind.queen.trail import (
    CorruptSegmentError,
    ForeignSegmentError,
    TrailSegmentReceiver,
    UnknownSegmentFormatError,
)
from waggle.envelope import Envelope
from waggle.errors import SignatureError
from waggle.ids import CellId, NodeId, new_event_id
from waggle.messages.swarm import TrailSegmentSync

log = get_logger(__name__)

ENVELOPE_REFUSED_KIND = "guard.envelope_refused"
SEGMENT_REFUSED_KIND = "guard.segment_refused"

__all__ = ["ENVELOPE_REFUSED_KIND", "SEGMENT_REFUSED_KIND", "LinkRefusals", "SegmentRefusal"]


class SegmentRefusal(StrEnum):
    """Why a trail segment chunk from a Cell's link was not merged."""

    ANOTHER_NODE = "another_node"  # It names a node or Warden its link never proved.
    ANOTHER_CELL = "another_cell"  # It names a Cell its link never proved: routed as that Cell's.
    FORMAT = "format"  # A segment format this Hive does not read.
    CORRUPT = "corrupt"  # Its bytes, digest or event count do not match what it declared.


class LinkRefusals:
    """Record one attached link's refusals on the Queen's trail, each kind and reason once."""

    def __init__(self, recorder: TrailRecorder | None, cell_id: CellId, node_id: NodeId) -> None:
        """Build the refusals of the link `cell_id`'s Warden proved as `node_id`.

        Args:
            recorder: The Queen's trail and identity; None records nothing.
            cell_id: The Cell the link's handshake proved; every event is about it.
            node_id: The node the link's frames are signed as, named on every event.
        """
        self._recorder = recorder
        self._cell_id = cell_id
        self._node_id = node_id
        self._recorded: set[tuple[str, str]] = set()  # Codingrules 8.5: grows only in `_record`.

    async def envelope(self, error: SignatureError) -> None:
        """Record that the link ended on a frame whose signature failed.

        Args:
            error: What the codec raised; its code's last part is the reason (`invalid`,
                `missing` or `unknown_node`), the same words the spec's error table uses.
        """
        await self._record(ENVELOPE_REFUSED_KIND, error.code.rsplit(".", 1)[-1])

    async def merge(
        self, receiver: TrailSegmentReceiver, envelope: Envelope, chunk: TrailSegmentSync
    ) -> None:
        """Hand `chunk` to `receiver` if its link may ship it; record why, if it was refused.

        Args:
            receiver: Reassembles and merges the Cell's segment into the Queen's trail.
            envelope: The envelope `chunk` arrived in, whose node and sender the link proved.
            chunk: One slice of the Cell's trail segment export.
        """
        refusal = await _merged(receiver, envelope, chunk, self._cell_id)
        if refusal is not None:
            await self._record(SEGMENT_REFUSED_KIND, refusal.value)

    async def _record(self, kind: str, reason: str) -> None:
        """Record `kind` about this link's Cell, unless there is no recorder or it already was."""
        if self._recorder is None or (kind, reason) in self._recorded:
            return
        self._recorded.add((kind, reason))
        recorder = self._recorder
        event = GuardEvent(
            id=new_event_id(recorder.clock),
            hive_id=recorder.hive_id,
            node_id=recorder.node_id,  # The Queen's own record, never the Cell's segment.
            at=recorder.clock.now(),
            actor="system",
            kind=kind,
            subject_id=self._cell_id,
            payload={"cell_id": self._cell_id, "node_id": self._node_id, "reason": reason},
        )
        await recorder.trail.record(event)
        log.warning("queen.link_refused", kind=kind, cell_id=self._cell_id, reason=reason)


async def _merged(
    receiver: TrailSegmentReceiver, envelope: Envelope, chunk: TrailSegmentSync, cell_id: CellId
) -> SegmentRefusal | None:
    """Merge `chunk` when its link (proved as `cell_id`) may ship it; return why not, or None."""
    # The receiver rule: only the envelope's own node and sender are proved by the signature.
    if chunk.node_id != envelope.node_id or chunk.warden_id != envelope.sender:
        return SegmentRefusal.ANOTHER_NODE
    # The receiver routes by the chunk's Cell; only the Cell the handshake proved may be named.
    if chunk.cell_id != cell_id:
        return SegmentRefusal.ANOTHER_CELL
    try:
        await receiver.receive(chunk)
    except UnknownSegmentFormatError:
        return SegmentRefusal.FORMAT
    except ForeignSegmentError:
        return SegmentRefusal.ANOTHER_NODE  # A proved chunk carrying another node's segment.
    except CorruptSegmentError:
        return SegmentRefusal.CORRUPT
    return None
