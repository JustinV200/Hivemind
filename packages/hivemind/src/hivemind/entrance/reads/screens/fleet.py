"""Read the Fleet list: every Cell joined with its Warden, work, Alarms, Cell Wax and hosting.

The Fleet (roadmap 12.6) is the widest screen: it needs the census's Cells and Wardens, the
unfinished tasks (for each Cell's current task and role), the open Alarms placed on Cells, the
written Cell Wax, the hosting plans, the telemetry samples (for the thoughts links) and the start of
each open lease. This module reads them once each and hands them to the pure ``fleet_view``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.fleet``. Calls into the census, the shared gathers and
    the pure builder.

Key invariants:
    - Only reads.

See Also:
    - hivemind.observation.api.fleet for the row each Cell becomes.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.reads.census import cell_views, warden_views
from hivemind.entrance.reads.screens.gather import (
    alarm_views,
    hosting_for,
    lease_starts,
    telemetry_of,
    unfinished_tasks,
    wax_notes,
)
from hivemind.memory import WaxState
from hivemind.observation import FleetInputs, FleetView, fleet_view

__all__ = ["read_fleet"]


async def read_fleet(reads: HiveReads, now: datetime) -> FleetView:
    """Read the Fleet list.

    Args:
        reads: The stores and live tables the Entrance reads.
        now: The instant the view is built for.

    Returns:
        One row per Cell and the tier legend.
    """
    cells = await cell_views(reads)
    tasks = await unfinished_tasks(reads)
    inputs = FleetInputs(
        cells=cells,
        wardens=warden_views(reads),
        tasks=tasks,
        alarms=await alarm_views(reads, tasks),
        wax=await wax_notes(reads, None, frozenset({WaxState.WRITTEN})),
        hosting=hosting_for(reads, cells),
        samples=telemetry_of(reads),
        lease_since=await lease_starts(reads, cells),
        as_of=now,
    )
    return fleet_view(inputs)
