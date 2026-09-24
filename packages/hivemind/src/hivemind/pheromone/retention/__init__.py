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
the decorator); importers keep naming this face.

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
    - SKELETON_KINDS, ATTESTED_KIND, SUMMARY_KIND, TierCount, skeleton_event, tier_counts: what
      survives a Night Veil Cell and the per-tier Capping counts (skeleton).
    - EphemeralSegments, Veiling, TakenSegment: the Queen-side ephemeral segments (segments).
    - VeiledTrail: the trail decorator every Queen-side writer records through (trail).
    - NightVeilTeardownPurge, PurgeReport, TrailRecorder, SegmentPurge, SqliteSegmentPurge,
      MemorySegmentPurge, SideChannelPurger, SideChannels, SIDE_CHANNEL_TIMEOUT_S: the purge.
"""

from hivemind.pheromone.retention.purge import (
    SIDE_CHANNEL_TIMEOUT_S,
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
    skeleton_event,
    tier_counts,
)
from hivemind.pheromone.retention.trail import VeiledTrail

__all__ = [
    "ATTESTED_KIND",
    "SIDE_CHANNEL_TIMEOUT_S",
    "SKELETON_KINDS",
    "SUMMARY_KIND",
    "EphemeralSegments",
    "MemorySegmentPurge",
    "NightVeilTeardownPurge",
    "PurgeReport",
    "SegmentPurge",
    "SideChannelPurger",
    "SideChannels",
    "SqliteSegmentPurge",
    "TakenSegment",
    "TierCount",
    "TrailRecorder",
    "VeiledTrail",
    "Veiling",
    "skeleton_event",
    "tier_counts",
]
