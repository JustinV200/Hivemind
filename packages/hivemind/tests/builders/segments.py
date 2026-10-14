"""Build the TrailSegmentSync chunks of a real export, signed or not, for the Queen's receivers.

A remote Warden ships its trail segment to the Queen as `TrailSegmentSync` chunks, and from phase
11 a Swarm node signs the final chunk with its own key (ADR-0053). The receiver's tests, the link
refusals' and the swarm gate's all need the same thing: the chunks a real `WaggleTrailSync` cuts
from a real segment, and the same chunks with the final one signed by a key a test chooses, or
tampered with after signing. `segment_chunks` makes the first (the sender's own code produces
them, so nothing about the wire shape is guessed), `signed` the second (the signature is made over
`waggle.messages.swarm.segment_signing_bytes`, the one definition of what is signed), and
`SegmentRig` bundles the ids a test needs to speak as one Warden on one node.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/attach, tests/unit/queen/trail and tests/unit/queen/swarm_gate.

Key invariants:
    - Chunks come from the real sender (`WaggleTrailSync`) over a memory transport, so a test
      exercises the wire shape the Queen will really see.
    - `signed` signs only the final chunk, the only one a signature may ride on.

See Also:
    - hivemind.wardens.trail_sync for the sender these chunks come from.
    - hivemind.queen.trail.signature for the check they are graded by.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

from hivemind.pheromone import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens import trail_sync as wardens_trail_sync
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.ids import (
    CellId,
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.swarm import TrailSegmentSync, segment_signing_bytes
from waggle.signing import Ed25519Signer
from waggle.transport.memory import MemoryTransport

__all__ = ["SegmentRig", "make_segment_rig", "segment_chunks", "signed"]


@dataclass(frozen=True, slots=True)
class SegmentRig:
    """The ids of one Warden on one node, and the key that node signs with."""

    clock: FakeClock
    hive_id: HiveId
    cell_id: CellId
    node_id: NodeId
    warden_id: WardenId
    signer: Ed25519Signer = field(default_factory=Ed25519Signer.generate)


def make_segment_rig(clock: FakeClock | None = None) -> SegmentRig:
    """Make the ids of a fresh Warden on a fresh node with a fresh key."""
    active = clock if clock is not None else FakeClock()
    return SegmentRig(
        clock=active,
        hive_id=new_hive_id(active),
        cell_id=new_cell_id(active),
        node_id=new_node_id(active),
        warden_id=new_warden_id(active),
    )


async def segment_chunks(
    rig: SegmentRig, *, events: int = 1, chunk_bytes: int | None = None
) -> list[TrailSegmentSync]:
    """Cut a real export of `events` events recorded on the rig's node into chunks.

    Args:
        rig: Whose segment it is.
        events: How many `cell.provisioned` events the segment holds.
        chunk_bytes: The chunk size to cut at (a small number forces several chunks); the
            sender's own default when omitted.

    Returns:
        Every chunk the real sender produced, in order, the last one `final` and unsigned.
    """
    source = MemoryPheromoneTrail(rig.clock)
    for _ in range(events):
        await source.record(_event(rig))
    sender, receiver = MemoryTransport.pair(Codec(), Codec())
    deps = TrailSyncDeps(
        trail=source,
        transport=sender,
        node_id=rig.node_id,
        cell_id=rig.cell_id,
        warden_id=rig.warden_id,
        hive_id=rig.hive_id,
        clock=rig.clock,
    )
    if chunk_bytes is None:
        await WaggleTrailSync(deps).sync()
    else:
        # The sender cuts at a module constant; a small one forces several chunks. Nothing else
        # runs while the export is cut, so patching it for the length of the call is safe.
        with patch.object(wardens_trail_sync, "MAX_CHUNK_BYTES", chunk_bytes):
            await WaggleTrailSync(deps).sync()
    await receiver.close()
    return [
        envelope.payload
        async for envelope in receiver.receive()
        if isinstance(envelope.payload, TrailSegmentSync)
    ]


def signed(
    chunks: list[TrailSegmentSync], signer: Ed25519Signer, **overrides: object
) -> list[TrailSegmentSync]:
    """Return `chunks` with the final one signed by `signer`, as a Swarm node's Warden does.

    Args:
        chunks: A real export's chunks (`segment_chunks`).
        signer: The key to sign with.
        **overrides: Fields to replace on the final chunk *after* signing, to tamper with a signed
            segment (a different `event_count`, say).

    Returns:
        The same chunks, the last one carrying `segment_signature`.
    """
    *rest, final = chunks
    assert final.sha256 is not None
    body = segment_signing_bytes(
        final.node_id,
        final.first_event_id,
        final.last_event_id,
        final.event_count,
        final.sha256,
    )
    signature = signer.sign(body)
    return [*rest, final.model_copy(update={"segment_signature": signature, **overrides})]


def _event(rig: SegmentRig) -> CellEvent:
    """A well-formed `cell.provisioned` recorded on the rig's node about its Cell."""
    return CellEvent(
        id=new_event_id(rig.clock),
        hive_id=rig.hive_id,
        node_id=rig.node_id,
        at=rig.clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=rig.cell_id,
        payload={},
    )
