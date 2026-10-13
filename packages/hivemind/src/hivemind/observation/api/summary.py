"""Build the summary strip: the core counters the Observation Hive always shows.

Codingrules 8.11 keeps six numbers visible on every screen: total active bees, active versus
inactive Cells, Real versus Virtual Cells, and live LLM versus autopilot activity (bees in awake
episodes versus bees handled by deterministic autopilot). They are derived, not stored, so this
module defines each one exactly. A Warden is an active bee while its newest Heartbeat says ACTIVE or
WATCH (and the Queen has not marked it offline); a sub-bee is active while its row says SPAWNED,
RUNNING or HANDING_OFF; the Queen herself is not counted. A Cell is active when its Warden reports
ACTIVE, and inactive otherwise (watching, clustered, offline, provisioning, dormant or with no
Warden). A bee is in an awake episode when its newest episode inside the activity window ran on a
model, and in autopilot when it only has autopilot decisions in the window: an awake episode lasts
seconds, so "recent" is the honest meaning of "current".

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built by
    ``hivemind.entrance.reads.screens.summary``; the metrics snapshot reuses it so the strip and
    the Prometheus gauges can never disagree. Pure: calls into the sibling views, the episode
    record and pydantic.

Key invariants:
    - Pure: every input is handed in.
    - ``active_bees`` is always ``active_wardens + active_sub_bees``; the Cells split sums to
      ``cells_total``; the Real and Virtual counts sum to it too.

See Also:
    - .claude/codingrules.md 8.11 for the strip this feeds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CellKind
from hivemind.memory import EpisodeRecord
from hivemind.observation.api.alarms import OpenAlarmView
from hivemind.observation.views.cells import CellView
from hivemind.observation.views.frames import TelemetrySampleView
from hivemind.observation.views.wardens import WardenView
from waggle.messages.supervision import WardenState, WorkerState

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

ACTIVITY_WINDOW_S = 60.0  # How recent an episode counts as "current": a few awake episodes' length.
# A Warden is an active bee while it is up and supervising; a Cell is active only while it works.
_ACTIVE_WARDENS = frozenset({WardenState.ACTIVE, WardenState.WATCH})
_ACTIVE_WORKERS = frozenset({WorkerState.SPAWNED, WorkerState.RUNNING, WorkerState.HANDING_OFF})

__all__ = ["ACTIVITY_WINDOW_S", "SummaryInputs", "SummaryView", "summary_view"]


class SummaryView(BaseModel):
    """The summary strip's counters (observe)."""

    model_config = _CONFIG

    as_of: datetime = Field(description="When the counters were computed.")
    active_bees: int = Field(description="Active Wardens plus active sub-bees (the Queen is not).")
    active_wardens: int = Field(description="Wardens whose newest state is ACTIVE or WATCH.")
    active_sub_bees: int = Field(description="Sub-bees SPAWNED, RUNNING or HANDING_OFF.")
    cells_total: int = Field(description="Every Cell the Hive knows.")
    cells_active: int = Field(description="Cells whose Warden reports ACTIVE.")
    cells_inactive: int = Field(description="Every other Cell.")
    cells_real: int = Field(description="Real (borrowed) Cells.")
    cells_virtual: int = Field(description="Virtual (provisioned) Cells.")
    bees_awake: int = Field(description="Bees with an awake (LLM) episode in the window.")
    bees_autopilot: int = Field(description="Bees with only autopilot decisions in the window.")
    activity_window_s: float = Field(description="The window, in seconds, 'current' means.")
    open_alarms: int = Field(description="Open Alarms, all severities.")


@dataclass(frozen=True, slots=True)
class SummaryInputs:
    """The gathered records the strip's counters are derived from.

    Attributes:
        cells: Every Cell's view.
        wardens: Every attached Warden's view.
        samples: The newest telemetry sample per bee (the Wardens' own and their sub-bees').
        episodes: Recent episode records, in any order.
        alarms: The open Alarms.
        as_of: The instant the counters are for.
    """

    cells: Sequence[CellView]
    wardens: Sequence[WardenView]
    samples: Sequence[TelemetrySampleView]
    episodes: Sequence[EpisodeRecord]
    alarms: Sequence[OpenAlarmView]
    as_of: datetime


def summary_view(inputs: SummaryInputs) -> SummaryView:
    """Derive the summary strip from the gathered records.

    Args:
        inputs: The records the counters count.

    Returns:
        The counters.
    """
    cells, wardens = inputs.cells, inputs.wardens
    active_wardens = sum(1 for warden in wardens if warden.state in _ACTIVE_WARDENS)
    active_sub_bees = sum(1 for sample in inputs.samples if sample.worker_state in _ACTIVE_WORKERS)
    working = {warden.cell_id for warden in wardens if warden.state is WardenState.ACTIVE}
    by_kind = {kind: sum(1 for cell in cells if cell.kind is kind) for kind in CellKind}
    awake, autopilot = _activity(inputs.episodes, inputs.as_of)
    active_cells = sum(1 for cell in cells if cell.id in working)
    return SummaryView(
        as_of=inputs.as_of,
        active_bees=active_wardens + active_sub_bees,
        active_wardens=active_wardens,
        active_sub_bees=active_sub_bees,
        cells_total=len(cells),
        cells_active=active_cells,
        cells_inactive=len(cells) - active_cells,
        cells_real=by_kind[CellKind.REAL],
        cells_virtual=by_kind[CellKind.VIRTUAL],
        bees_awake=awake,
        bees_autopilot=autopilot,
        activity_window_s=ACTIVITY_WINDOW_S,
        open_alarms=len(inputs.alarms),
    )


def _activity(episodes: Sequence[EpisodeRecord], as_of: datetime) -> tuple[int, int]:
    """Count bees with a recent awake episode, and bees with only recent autopilot decisions."""
    since = as_of - timedelta(seconds=ACTIVITY_WINDOW_S)
    recent = [record for record in episodes if since <= record.at <= as_of]
    awake = {record.principal for record in recent if not record.is_autopilot}
    autopilot = {record.principal for record in recent if record.is_autopilot} - awake
    return len(awake), len(autopilot)
