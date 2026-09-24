"""Build the Night Veil boundary over an in-memory trail, for tests below the composition root.

`hivemind.cli.compose.night_veil.build_night_veil` builds the real boundary from a manifest, over
the Hive's SQLite trail. A unit test of the lifecycle, the Queen's attach or the segment receiver
wants the same parts over a `MemoryPheromoneTrail` instead: `make_night_veil` wires them exactly
as the composition root does (the ephemeral segments, the `VeiledTrail` every writer records
through, and the purge that records past the boundary on the durable trail), with no side
channels, which is also what production registers today.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - The purge's recorder writes to the durable trail, never to the `VeiledTrail`.

See Also:
    - hivemind.cli.compose.night_veil for the production wiring this mirrors.
    - hivemind.hive.night_veil.boundary for NightVeilBoundary.
"""

from __future__ import annotations

from hivemind.cell import CellIdentity
from hivemind.hive.night_veil import NightVeilBoundary
from hivemind.pheromone import (
    EphemeralSegments,
    MemoryPheromoneTrail,
    MemorySegmentPurge,
    NightVeilTeardownPurge,
    TrailRecorder,
    VeiledTrail,
)
from waggle.clock import Clock

__all__ = ["make_night_veil"]


def make_night_veil(
    durable: MemoryPheromoneTrail, clock: Clock, identity: CellIdentity
) -> NightVeilBoundary:
    """Build a NightVeilBoundary over `durable`, the way the composition root builds the real one.

    Args:
        durable: The Hive's trail; the boundary's `veiled` trail wraps it.
        clock: Stamps every record the purge makes.
        identity: The Queen's own Hive and node the purge records as.

    Returns:
        A boundary whose `veiled` trail a lifecycle or Queen under test records through.
    """
    segments = EphemeralSegments(clock)
    recorder = TrailRecorder(
        trail=durable, clock=clock, hive_id=identity.hive_id, node_id=identity.node_id
    )
    purge = NightVeilTeardownPurge(MemorySegmentPurge(durable), (), recorder, ephemeral=segments)
    return NightVeilBoundary(
        segments=segments,
        veiled=VeiledTrail(durable, segments),
        purge=purge,
        recorder=recorder,
    )
