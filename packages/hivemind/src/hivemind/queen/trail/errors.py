"""Define the typed errors a TrailSegmentSync export can be refused with.

A remote Warden ships its trail segment (the slice of the Pheromone Trail, the Hive's append-only
audit log, that it wrote on its own node) to the Queen as `TrailSegmentSync` chunks, and the Queen
merges it into her trail. Merging writes into the audit record, so an export is refused, whole and
with a typed error, whenever it cannot be trusted to be what its sender says: a format this Queen
does not read, bytes that do not match what the final chunk declared, another node's segment under
this node's name, or, from phase 11 (ADR-0053), a signature the sending node's registered key does
not vouch for. Each error names the node and says why, never what the segment held; the gate that
drains the link turns the error into a `guard.segment_refused` record with a short reason code.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.trail`.
    Raised by `hivemind.queen.trail.sync.TrailSegmentReceiver` and `.signature`; caught by the
    gates' link refusals (`hivemind.queen.attach.refusals`). Calls into `hivemind.wardens.
    trail_sync` (the format version) and waggle (ids) only.

Key invariants:
    - Every error here is a `SegmentSyncError`, so a caller that only cares that an export was not
      mergeable catches one type.
    - No message carries an event, a payload or a key: node ids, versions and sizes only.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the signature rule.
    - hivemind.queen.trail.signature for the check that raises the two signature errors.
"""

from __future__ import annotations

from hivemind.wardens.trail_sync import SEGMENT_FORMAT_VERSION
from waggle.ids import NodeId

__all__ = [
    "BadSegmentSignatureError",
    "CorruptSegmentError",
    "ForeignSegmentError",
    "SegmentSyncError",
    "UnknownSegmentFormatError",
    "UnsignedSegmentError",
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


class ForeignSegmentError(CorruptSegmentError):
    """A reassembled export is another node's segment than the node its chunk was shipped as."""


class UnsignedSegmentError(SegmentSyncError):
    """A node whose segments must be signed shipped one without a signature."""

    def __init__(self, node_id: NodeId) -> None:
        """Name the node whose unsigned export was refused."""
        super().__init__(
            f"TrailSegmentSync from node {node_id} carries no segment_signature, and this node's "
            "segments must be signed by its registered key."
        )
        self.node_id = node_id


class BadSegmentSignatureError(SegmentSyncError):
    """A segment's signature did not verify against the key registered for the sending node."""

    def __init__(self, node_id: NodeId, detail: str) -> None:
        """Name the node whose export was refused and why its signature failed."""
        super().__init__(
            f"TrailSegmentSync from node {node_id} is not signed by its registered key: {detail}"
        )
        self.node_id = node_id
