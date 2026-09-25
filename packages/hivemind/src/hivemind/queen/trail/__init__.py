"""Re-export record_event, record_forage_event and TrailSegmentReceiver: the queen trail package.

Everything the Queen does to the Pheromone Trail lives here: `.record` builds the one shape every
`queen.*` and `forage.*` event this Queen writes shares, and `.sync` reassembles the segment a
Warden inside a Virtual Cell (ADR-0027) ships over its own Queen link and merges it into the same
log. Grouped into a package by codingrules 5.6 ("a source directory holds at most ten modules"),
which `hivemind/queen/` reached once roadmap steps 5.0b/5.0d added `goal_submission` and
`leave_memory` beside this dispatch's own modules; the two were already one concept.

Importers keep naming the package, never a module inside it: `from hivemind.queen.trail import
record_event` read the same before the split, and the old `hivemind.queen.trail_sync`'s own
two importers now read `from hivemind.queen.trail import TrailSegmentReceiver`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen`. Imported
    by every `hivemind.queen` module that records an event, and by `hivemind.queen.cell_gate.
    listener`/`hivemind.cli.compose.virtual_cells` for the receiver. Calls into
    `hivemind.pheromone`, `hivemind.queen.deps` and `hivemind.wardens.trail_sync` only.

Key invariants:
    - This module contains no logic of its own: it is a face over `.record` and `.sync`
      (codingrules 5.2).

See Also:
    - .claude/codingrules.md section 12 for the trail-event shape and the per-node-segment rule.
    - hivemind.wardens.trail_sync for WaggleTrailSync, the sender `.sync` reassembles chunks from.

Public API:
    - record_event, record_forage_event: the Queen's own event writers; queen_event: the same
      queen.* shape built without recording it, for a store that writes it with its row (record).
    - TrailSegmentReceiver, SegmentSyncError, CorruptSegmentError, ForeignSegmentError,
      UnknownSegmentFormatError: the remote-segment receiver and its typed errors (sync).
"""

from hivemind.queen.trail.record import queen_event, record_event, record_forage_event
from hivemind.queen.trail.sync import (
    CorruptSegmentError,
    ForeignSegmentError,
    SegmentSyncError,
    TrailSegmentReceiver,
    UnknownSegmentFormatError,
)

__all__ = [
    "CorruptSegmentError",
    "ForeignSegmentError",
    "SegmentSyncError",
    "TrailSegmentReceiver",
    "UnknownSegmentFormatError",
    "queen_event",
    "record_event",
    "record_forage_event",
]
