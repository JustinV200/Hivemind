"""Define the metrics snapshot: every figure the Hive's Prometheus endpoint exposes, as one value.

Operators watch the Hive from outside (a Grafana board, an alert rule), so the Observation Hive
publishes the same facts its screens show as numbers: Cells by kind and status, Wardens by where
they run, Forage grants and headroom, open Alarms by kind and level, awake episodes per hour per
bee, tasks by status, tool promotions, Guard denials, and tokens and cost per goal, slot and
provider, plus the first-class counters the summary strip shows (active bees, active and inactive
Cells, Real and Virtual totals, bees in awake episodes against bees in autopilot). The snapshot is
one frozen model built by one pure function from the records the Entrance gathers; the text format
is rendered from it elsewhere, so what Prometheus scrapes and what the screens show can never
drift apart: the summary counters are the summary strip's own.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.metrics``. Built by
    ``hivemind.entrance.reads.screens.metrics``; rendered by ``exposition``. Pure: calls into the
    sibling views and builders and pydantic.

Key invariants:
    - Pure: every figure comes from an argument.
    - Counts that come from the trail cover the newest ``MAX_QUERY_LIMIT`` events of their kind.
    - The summary counters are exactly ``summary_view``'s.

See Also:
    - hivemind.observation.api.summary for the counters this snapshot embeds.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import TaskStatus
from hivemind.memory import EpisodeRecord
from hivemind.observation.api.alarms import OpenAlarmView
from hivemind.observation.api.cost import CostLine
from hivemind.observation.api.fleet import cell_status, warden_runs
from hivemind.observation.api.summary import SummaryView
from hivemind.observation.views.cells import CellView
from hivemind.observation.views.forage import GrantView, HeadroomView, ReserveView
from hivemind.observation.views.wardens import WardenView

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

EPISODE_WINDOW = timedelta(hours=1)  # "Per hour": the window the episode rates count over.
UNKNOWN_LOCATION = "unknown"  # A Warden whose runtime level is not tracked.
MAX_COST_SERIES = 500  # Most (goal, slot, provider) series exposed: Prometheus cardinality bound.

__all__ = [
    "EPISODE_WINDOW",
    "MAX_COST_SERIES",
    "UNKNOWN_LOCATION",
    "AlarmCount",
    "CellCount",
    "GrantTotals",
    "MetricsInputs",
    "MetricsSnapshot",
    "PrincipalCount",
    "StatusCount",
    "WardenCount",
    "metrics_snapshot",
]


class CellCount(BaseModel):
    """Cells of one kind in one status."""

    model_config = _CONFIG

    kind: str = Field(description="real or virtual.")
    status: str = Field(description="The Cell's headline status, lower case.")
    count: int = Field(description="How many.")


class WardenCount(BaseModel):
    """Wardens running in one place."""

    model_config = _CONFIG

    location: str = Field(description="hive_stand, on_cell or unknown.")
    count: int = Field(description="How many.")


class GrantTotals(BaseModel):
    """The Forage ledger's grants and headroom, summed."""

    model_config = _CONFIG

    live: int = Field(description="Live grants.")
    tokens_issued: int = Field(description="Tokens they allow.")
    tokens_spent: int = Field(description="Tokens spent against them.")
    spend_issued_usd: float = Field(description="Spend they allow.")
    spent_usd: float = Field(description="Spend charged to them.")
    headroom_sub_bees: int = Field(description="Shared sub-bee slots free.")
    headroom_shared_seats: int = Field(description="Shared model seats free.")
    reserve_seats: int = Field(description="Seats the Royal Reserve holds back.")


class AlarmCount(BaseModel):
    """Open Alarms of one kind and level."""

    model_config = _CONFIG

    kind: str = Field(description="The Alarm kind, lower case.")
    level: str = Field(description="info, warning or critical.")
    count: int = Field(description="How many are open.")


class StatusCount(BaseModel):
    """Tasks in one status."""

    model_config = _CONFIG

    status: str = Field(description="The task status, lower case.")
    count: int = Field(description="How many.")


class PrincipalCount(BaseModel):
    """One bee's episodes in the last hour."""

    model_config = _CONFIG

    principal: str = Field(description="The bee's id or role.")
    awake: int = Field(description="Awake (LLM) episodes.")
    autopilot: int = Field(description="Deterministic autopilot decisions.")


class MetricsSnapshot(BaseModel):
    """Every figure the Prometheus endpoint exposes (observe)."""

    model_config = _CONFIG

    summary: SummaryView = Field(description="The summary strip's counters.")
    cells: list[CellCount] = Field(description="Cells by kind and status.")
    wardens: list[WardenCount] = Field(description="Wardens by location.")
    grants: GrantTotals = Field(description="Grants and headroom.")
    alarms: list[AlarmCount] = Field(description="Open Alarms by kind and level.")
    episodes: list[PrincipalCount] = Field(description="Episodes in the last hour, per bee.")
    tasks: list[StatusCount] = Field(description="Tasks by status.")
    tool_promotions: int = Field(description="tool.promoted events on the trail.")
    denials: int = Field(description="guard.denied events on the trail.")
    llm: list[CostLine] = Field(description="Tokens and cost per goal, slot and provider.")


@dataclass(frozen=True, slots=True)
class MetricsInputs:
    """The gathered records a snapshot is built from.

    Attributes:
        summary: The summary strip's counters, already derived.
        cells: Every Cell's view.
        wardens: Every attached Warden's view.
        alarms: The open Alarms.
        grants: The live grants.
        headroom: The shared pool's headroom.
        reserve: The Royal Reserve.
        episodes: Recent episode records.
        task_counts: Tasks by status.
        tool_promotions: ``tool.promoted`` events counted on the trail.
        denials: ``guard.denied`` events counted on the trail.
        cost_lines: Spend per goal, slot and provider.
        as_of: The instant the snapshot is for.
    """

    summary: SummaryView
    cells: Sequence[CellView]
    wardens: Sequence[WardenView]
    alarms: Sequence[OpenAlarmView]
    grants: Sequence[GrantView]
    headroom: HeadroomView
    reserve: ReserveView
    episodes: Sequence[EpisodeRecord]
    task_counts: Mapping[TaskStatus, int]
    tool_promotions: int
    denials: int
    cost_lines: Sequence[CostLine]
    as_of: datetime


def metrics_snapshot(inputs: MetricsInputs) -> MetricsSnapshot:
    """Build the snapshot.

    Args:
        inputs: The gathered records.

    Returns:
        Every figure the endpoint exposes; zero or empty families on an empty Hive.
    """
    return MetricsSnapshot(
        summary=inputs.summary,
        cells=_cells(inputs),
        wardens=_wardens(inputs.cells),
        grants=_grants(inputs),
        alarms=_alarms(inputs.alarms),
        episodes=_episodes(inputs.episodes, inputs.as_of),
        tasks=[
            StatusCount(status=status.value.lower(), count=inputs.task_counts.get(status, 0))
            for status in TaskStatus
        ],
        tool_promotions=inputs.tool_promotions,
        denials=inputs.denials,
        llm=_costliest(inputs.cost_lines),
    )


def _cells(inputs: MetricsInputs) -> list[CellCount]:
    """Count Cells per (kind, status), sorted for a stable exposition."""
    wardens: dict[str, WardenView] = {warden.id: warden for warden in inputs.wardens}
    counted = Counter(
        (cell.kind.value.lower(), cell_status(cell, wardens.get(cell.warden_id or "")).lower())
        for cell in inputs.cells
    )
    return [
        CellCount(kind=kind, status=status, count=n)
        for (kind, status), n in sorted(counted.items())
    ]


def _wardens(cells: Sequence[CellView]) -> list[WardenCount]:
    """Count the attached Wardens per place they run."""
    counted = Counter(
        warden_runs(cell) or UNKNOWN_LOCATION for cell in cells if cell.warden_id is not None
    )
    return [WardenCount(location=place, count=n) for place, n in sorted(counted.items())]


def _grants(inputs: MetricsInputs) -> GrantTotals:
    """Sum the live grants and carry the headroom and reserve."""
    grants = inputs.grants
    return GrantTotals(
        live=len(grants),
        tokens_issued=sum(grant.token_budget for grant in grants),
        tokens_spent=sum(grant.tokens_spent for grant in grants),
        spend_issued_usd=sum(grant.spend_budget for grant in grants),
        spent_usd=sum(grant.spent for grant in grants),
        headroom_sub_bees=inputs.headroom.sub_bees,
        headroom_shared_seats=inputs.headroom.shared_seats,
        reserve_seats=inputs.reserve.seats,
    )


def _alarms(alarms: Sequence[OpenAlarmView]) -> list[AlarmCount]:
    """Count open Alarms per kind and level."""
    counted = Counter((alarm.kind.lower(), alarm.severity.value.lower()) for alarm in alarms)
    return [
        AlarmCount(kind=kind, level=level, count=n) for (kind, level), n in sorted(counted.items())
    ]


def _episodes(episodes: Sequence[EpisodeRecord], as_of: datetime) -> list[PrincipalCount]:
    """Count each bee's awake episodes and autopilot decisions inside the last hour."""
    since = as_of - EPISODE_WINDOW
    recent = [record for record in episodes if since <= record.at <= as_of]
    awake = Counter(record.principal for record in recent if not record.is_autopilot)
    autopilot = Counter(record.principal for record in recent if record.is_autopilot)
    return [
        PrincipalCount(principal=bee, awake=awake[bee], autopilot=autopilot[bee])
        for bee in sorted(set(awake) | set(autopilot))
    ]


def _costliest(lines: Sequence[CostLine]) -> list[CostLine]:
    """Keep the costliest series, so a long-lived Hive cannot grow the exposition without bound."""
    ranked = sorted(lines, key=lambda line: (-line.cost_usd, line.goal_id or "", line.slot))
    return ranked[:MAX_COST_SERIES]
