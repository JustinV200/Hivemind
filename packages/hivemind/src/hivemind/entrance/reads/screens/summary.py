"""Read the summary strip: the Observation Hive's always-visible counters.

The strip (codingrules 8.11) is the cheapest screen and the one every page shows, so it reads only
what its counters need: every Cell and Warden (the census), the newest telemetry sample of every
bee, recent episode records and the open Alarms. Nothing here places an Alarm on a Cell: the strip
counts them, and the Fleet shows where they are.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.summary`` and by the metrics read. Calls into the census,
    the shared gathers and the pure ``summary_view``.

Key invariants:
    - Only reads.

See Also:
    - hivemind.observation.api.summary for each counter's definition.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.reads.census import cell_views, warden_views
from hivemind.entrance.reads.screens.gather import alarm_views, recent_episodes, telemetry_of
from hivemind.observation import SummaryInputs, SummaryView, summary_view

SUMMARY_EPISODES = 500  # Newest episode records read: far more than the activity window holds.

__all__ = ["SUMMARY_EPISODES", "read_summary"]


async def read_summary(reads: HiveReads, now: datetime) -> SummaryView:
    """Read the summary strip's counters.

    Args:
        reads: The stores and live tables the Entrance reads.
        now: The instant the counters are for (the Entrance's clock).

    Returns:
        The counters.
    """
    cells = await cell_views(reads)
    episodes = await recent_episodes(reads, SUMMARY_EPISODES)
    alarms = await alarm_views(reads)
    inputs = SummaryInputs(
        cells=cells,
        wardens=warden_views(reads),
        samples=telemetry_of(reads),
        episodes=episodes,
        alarms=alarms,
        as_of=now,
    )
    return summary_view(inputs)
