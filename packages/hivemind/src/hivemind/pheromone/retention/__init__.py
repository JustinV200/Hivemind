"""Provide the Night Veil boundary: an ephemeral segment per Cell, a skeleton, a teardown purge.

Codingrules section 12: a Night Veil Cell's execution records live in an ephemeral segment keyed to
the Cell and are purged at teardown, and only the lifecycle skeleton survives on the Queen's trail.
This package is that boundary, split by responsibility (codingrules 5.2): `skeleton` names what
survives and cuts an event to it (pure); `segments` holds each living Night Veil Cell's segment in
the Queen's memory and knows which ids belong to which Cell; `trail` is the `PheromoneTrail`
decorator every Queen-side writer records through, which sends a Night Veil record's skeleton copy
to the durable trail and the whole record to its Cell's segment; `purge` ends a Cell, taking its
segment, keeping one Capping summary per tier and recording `cell.purged`, and is the package's one
removal path. It was a single module until the boundary grew its production half (the segments and
the decorator); importers keep naming this face. `checkpoint` keeps, per living Night Veil Cell,
the counts and ids a restarted Queen needs to summarise and purge it, and nothing else.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone`. Built by the
    composition root (`hivemind.cli.compose.night_veil`); driven by `hivemind.hive.night_veil.
    boundary`, `hivemind.queen` (the Warden attach, goal submission, the Virtual Cell provider,
    the segment receiver) and `hivemind.brood_chamber` (the task-event cut). Calls into
    `hivemind.common`, `hivemind.pheromone.events`, `hivemind.pheromone.trail` and waggle only.

Key invariants:
    - This face holds re-exports only (codingrules 5.4).
    - `purge` is the only module in `hivemind.pheromone` that removes a row (ADR-0007).

See Also:
    - .claude/codingrules.md section 12 for the boundary.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the design.

Public API:
    - SKELETON_KINDS, ATTESTED_KIND, SUMMARY_KIND, TierCount, skeleton_event, tier_counts,
      merge_counts: what survives a Night Veil Cell and the per-tier Capping counts (skeleton).
    - EphemeralSegments, Veiling, TakenSegment: the Queen-side ephemeral segments (segments).
    - NightVeilCheckpoints, CellCheckpoint, TierTally, Checkpointer, MemoryCheckpoints,
      LazySqliteCheckpoints: a living Night Veil Cell's counts and ids, kept for a restarted
      Queen (checkpoint).
    - VeiledTrail, segments_of, query_cell: the trail decorator every Queen-side writer records
      through, the segments behind it, and one Cell's records read with its held segment (trail).
    - NightVeilTeardownPurge, PurgeReport, TrailRecorder, SegmentPurge, SqliteSegmentPurge,
      LazySqliteSegmentPurge, MemorySegmentPurge, SideChannelPurger, MemberSource, SideChannels,
      SIDE_CHANNEL_TIMEOUT_S: the purge.
"""

from hivemind.pheromone.retention.checkpoint import (
    CellCheckpoint,
    Checkpointer,
    LazySqliteCheckpoints,
    MemoryCheckpoints,
    NightVeilCheckpoints,
    TierTally,
)
from hivemind.pheromone.retention.purge import (
    SIDE_CHANNEL_TIMEOUT_S,
    LazySqliteSegmentPurge,
    MemberSource,
    MemorySegmentPurge,
    NightVeilTeardownPurge,
    PurgeReport,
    SegmentPurge,
    SideChannelPurger,
    SideChannels,
    SqliteSegmentPurge,
    TrailRecorder,
)
from hivemind.pheromone.retention.segments import EphemeralSegments, TakenSegment, Veiling
from hivemind.pheromone.retention.skeleton import (
    ATTESTED_KIND,
    SKELETON_KINDS,
    SUMMARY_KIND,
    TierCount,
    merge_counts,
    skeleton_event,
    tier_counts,
)
from hivemind.pheromone.retention.trail import VeiledTrail, query_cell, segments_of

__all__ = [
    "ATTESTED_KIND",
    "SIDE_CHANNEL_TIMEOUT_S",
    "SKELETON_KINDS",
    "SUMMARY_KIND",
    "CellCheckpoint",
    "Checkpointer",
    "EphemeralSegments",
    "LazySqliteCheckpoints",
    "LazySqliteSegmentPurge",
    "MemberSource",
    "MemoryCheckpoints",
    "MemorySegmentPurge",
    "NightVeilCheckpoints",
    "NightVeilTeardownPurge",
    "PurgeReport",
    "SegmentPurge",
    "SideChannelPurger",
    "SideChannels",
    "SqliteSegmentPurge",
    "TakenSegment",
    "TierCount",
    "TierTally",
    "TrailRecorder",
    "VeiledTrail",
    "Veiling",
    "merge_counts",
    "query_cell",
    "segments_of",
    "skeleton_event",
    "tier_counts",
]
