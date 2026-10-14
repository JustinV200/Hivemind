"""Build the Fleet list: every Cell in one table, tier and Mask state first, with a fixed legend.

The Fleet list (roadmap 12.6) answers "what is the Hive running on, and is it healthy?" at a
glance, so each row joins what the Cell's own view, its Warden, the Brood Chamber, the trail, Cell
Wax and the hosting plan each know: kind and source, a headline status and mode, the
``CombShieldLevel`` and Pheromone Mask state (never hidden: codingrules 8.11), the access level,
where the Warden runs and where its models come from (the two badges), the Cell's current task and
the role working it, its open Alarms, the highest live Cell Wax severity, the lease age and the ids
the UI links through (the Cell page, the Warden's Attendant view and each bee's thoughts). The
view carries the three-tier legend as a fixed field so a client always renders it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built from
    gathered records by ``hivemind.entrance.reads.screens.fleet``; the Cell page and the metrics
    reuse ``cell_status``, ``warden_runs`` and ``live_wax``. Pure: calls into the Cell, task and
    Cell Wax records, the sibling views and pydantic.

Key invariants:
    - Pure: every input is handed in; nothing is read here.
    - ``legend`` always lists MEADOW, PROPOLIS and NIGHT_VEIL, in that order.
    - A field no store can fill is null and says so; none is guessed.

See Also:
    - hivemind.observation.api.hosting for the models badge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import HIVE_STAND_SOURCE, AccessLevel, CellKind, CombShieldLevel
from hivemind.memory import CellWax, WaxSeverity, WaxState
from hivemind.observation.api.alarms import OpenAlarmView, alarms_on_cell
from hivemind.observation.api.hosting import HostingFacts, ModelsFrom, models_from
from hivemind.observation.views.cells import CellMode, CellView, MaskStateName
from hivemind.observation.views.frames import TelemetrySampleView
from hivemind.observation.views.wardens import WardenView
from waggle.messages.base import CellIdField, TaskIdField, WardenIdField
from waggle.messages.swarm import RuntimeLevel
from waggle.messages.task import WorkerRole

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

WardenRuns = Literal["hive_stand", "on_cell"]
UNKNOWN_STATUS = "UNKNOWN"  # A Cell with no lifecycle, Warden state or lease edge to name.
# Lowest to highest: the order Cell Wax severities weigh on placement.
_SEVERITY_ORDER = (WaxSeverity.NOTE, WaxSeverity.CAUTION, WaxSeverity.BLOCK)

__all__ = [
    "TIER_LEGEND",
    "UNKNOWN_STATUS",
    "FleetInputs",
    "FleetLinks",
    "FleetRow",
    "FleetView",
    "TierLegendEntry",
    "WardenRuns",
    "cell_status",
    "fleet_view",
    "highest_severity",
    "live_wax",
    "warden_runs",
    "warden_runs_at",
]


class TierLegendEntry(BaseModel):
    """One Comb Shield tier and what it means, for the legend the Fleet always shows."""

    model_config = _CONFIG

    tier: CombShieldLevel = Field(description="The tier.")
    meaning: str = Field(description="One line on what the tier requires of a Cell.")


TIER_LEGEND = (
    TierLegendEntry(tier=CombShieldLevel.MEADOW, meaning="Baseline: any machine, no VPN required."),
    TierLegendEntry(
        tier=CombShieldLevel.PROPOLIS, meaning="Hardened: OpenVPN required, Tor disabled."
    ),
    TierLegendEntry(
        tier=CombShieldLevel.NIGHT_VEIL,
        meaning="Virtual only: OpenVPN plus Tor, every slot local, torn down never kept.",
    ),
)


class FleetLinks(BaseModel):
    """The ids a Fleet row links through; the UI builds the routes from them."""

    model_config = _CONFIG

    cell_id: CellIdField = Field(description="Open the Cell page (/v1/cells/{id}/page).")
    attendant_principal: WardenIdField | None = Field(
        description="Open its Warden's Attendant view (/v1/attendant/{id}); null with no Warden."
    )
    thought_principals: list[str] = Field(
        description="The Warden and each sub-bee whose thoughts can be opened (/v1/thoughts)."
    )


class FleetRow(BaseModel):
    """One Cell in the Fleet list (observe)."""

    model_config = _CONFIG

    id: CellIdField = Field(description="The Cell's id.")
    name: str = Field(description="Its label, or a Virtual Cell's image.")
    kind: CellKind = Field(description="REAL or VIRTUAL, for the Real / Virtual / All filter.")
    source: str = Field(description="What produced it: hive_stand, swarm, or a backend.")
    status: str = Field(
        description="Headline status: a Virtual Cell's lifecycle status, else its Warden's "
        "state, else its lease state; UNKNOWN when none is known."
    )
    mode: CellMode | None = Field(description="ACTIVE or WATCH, when its Warden reported one.")
    comb_shield: CombShieldLevel = Field(description="Its security tier: always shown.")
    mask_state: MaskStateName | None = Field(
        description="Pheromone Mask state; null until roadmap 6.13 tracks it."
    )
    access_level: AccessLevel = Field(description="How much of it the Hive may touch.")
    warden_runs: WardenRuns | None = Field(
        description="Where its Warden runs: hive_stand, or on_cell (inside a Virtual Cell, or on "
        "a Swarm device once its Warden has moved onto it). Null with no Warden, and for a Swarm "
        "Cell whose registry rung this Hive does not read."
    )
    models_from: ModelsFrom = Field(
        description="Where its bees' models come from, from its hosting plan; unknown with none."
    )
    current_task_id: TaskIdField | None = Field(description="Its running task, else its first.")
    current_task_role: WorkerRole | None = Field(description="The role working that task.")
    open_alarms: int = Field(description="Open Alarms placed on it.")
    highest_wax_severity: WaxSeverity | None = Field(
        description="The highest severity among its live Cell Wax; null with none."
    )
    lease_age_s: float | None = Field(description="Seconds since its open lease began.")
    warden_id: WardenIdField | None = Field(description="Its Warden; null when none is attached.")
    links: FleetLinks = Field(description="The ids the row links through.")


class FleetView(BaseModel):
    """Every Cell, with the tier legend the view always shows (observe)."""

    model_config = _CONFIG

    as_of: datetime = Field(description="When the view was built.")
    rows: list[FleetRow] = Field(description="Every Cell: attached Wardens' first, then Virtual.")
    legend: list[TierLegendEntry] = Field(description="MEADOW, PROPOLIS and NIGHT_VEIL, always.")


@dataclass(frozen=True, slots=True)
class FleetInputs:
    """The gathered records a Fleet view is built from.

    Attributes:
        cells: Every Cell's view.
        wardens: Every attached Warden's view.
        tasks: The unfinished tasks placed on Cells.
        alarms: The open Alarms.
        wax: The Cell Wax notes in state WRITTEN (expiry is judged here).
        hosting: The hosting plans and the Forage map's hosts.
        samples: The newest telemetry sample per bee.
        lease_since: When each open Real Cell lease began.
        as_of: The instant the view is built for.
    """

    cells: Sequence[CellView]
    wardens: Sequence[WardenView]
    tasks: Sequence[Task]
    alarms: Sequence[OpenAlarmView]
    wax: Sequence[CellWax]
    hosting: HostingFacts
    samples: Sequence[TelemetrySampleView]
    lease_since: Mapping[str, datetime]
    as_of: datetime


def fleet_view(inputs: FleetInputs) -> FleetView:
    """Build the Fleet list.

    Args:
        inputs: The gathered records.

    Returns:
        One row per Cell and the fixed legend.
    """
    wardens: dict[str, WardenView] = {warden.id: warden for warden in inputs.wardens}
    rows = [_row(cell, wardens.get(cell.warden_id or ""), inputs) for cell in inputs.cells]
    return FleetView(as_of=inputs.as_of, rows=rows, legend=list(TIER_LEGEND))


def cell_status(cell: CellView, warden: WardenView | None) -> str:
    """Name a Cell's headline status from what is known of it.

    Args:
        cell: The Cell's view.
        warden: Its attached Warden's view, if any.

    Returns:
        The Virtual lifecycle status, else the Warden's state, else the lease state, else
        ``UNKNOWN``.
    """
    # A Virtual Cell's lifecycle is the most specific truth about it.
    if cell.virtual_status is not None:
        return cell.virtual_status.value
    if warden is not None and warden.state is not None:
        return warden.state.value
    if cell.lease_state is not None:
        return cell.lease_state.value
    return UNKNOWN_STATUS


def warden_runs(cell: CellView) -> WardenRuns | None:
    """Say where a Cell's Warden runs, as far as the records tell.

    Args:
        cell: The Cell's view.

    Returns:
        ``on_cell`` for a Virtual Cell's Warden (it runs inside it), ``hive_stand`` for the Hive
        Stand's own, and for a Swarm Cell the answer its registry rung gives (``warden_runs_at``);
        None with no Warden, or for a Swarm Cell whose rung was not read.
    """
    if cell.warden_id is None:
        return None
    if cell.virtual_status is not None:
        return "on_cell"
    # A Swarm Cell's Warden moves with its rung, so the registry's rung decides, not the source.
    if cell.runtime_level is not None:
        return warden_runs_at(cell.runtime_level)
    return "hive_stand" if cell.source == HIVE_STAND_SOURCE else None


def warden_runs_at(level: RuntimeLevel) -> WardenRuns:
    """Say where a Swarm device's Warden runs on a rung of the runtime ladder.

    The one rule every reader of a Swarm Cell shares (the Fleet, the Cell page, the metrics and
    ``hive cells list``): the Warden is the first bee to move onto a device (ADR-0052), so it runs
    on the Hive Stand on the bottom rung and on the device from the second rung up.

    Args:
        level: The rung the device is on.

    Returns:
        ``hive_stand`` at GATEWAY (Level 0); ``on_cell`` at COLONIZED and NUC (Levels 1 and 2).
    """
    return "hive_stand" if level is RuntimeLevel.GATEWAY else "on_cell"


def live_wax(wax: Sequence[CellWax], as_of: datetime) -> tuple[CellWax, ...]:
    """Keep the Cell Wax notes that are written and have not expired.

    Args:
        wax: Notes in any state.
        as_of: The instant to judge expiry at.

    Returns:
        The WRITTEN notes whose expiry, if any, is still ahead.
    """
    return tuple(
        note
        for note in wax
        if note.state is WaxState.WRITTEN and (note.expires_at is None or note.expires_at > as_of)
    )


def highest_severity(wax: Sequence[CellWax]) -> WaxSeverity | None:
    """Return the highest severity among some Cell Wax notes, or None for none.

    Args:
        wax: The notes.

    Returns:
        BLOCK over CAUTION over NOTE; None when empty.
    """
    return max((note.severity for note in wax), key=_SEVERITY_ORDER.index, default=None)


def _row(cell: CellView, warden: WardenView | None, inputs: FleetInputs) -> FleetRow:
    """Join one Cell's records into its row."""
    task = _current_task(cell, inputs.tasks)
    mine = [note for note in live_wax(inputs.wax, inputs.as_of) if note.cell_id == cell.id]
    since = inputs.lease_since.get(cell.id)
    return FleetRow(
        id=cell.id,
        name=cell.name,
        kind=cell.kind,
        source=cell.source,
        status=cell_status(cell, warden),
        mode=cell.mode,
        comb_shield=cell.comb_shield,
        mask_state=cell.mask_state,
        access_level=cell.access_level,
        warden_runs=warden_runs(cell),
        models_from=models_from(cell.id, inputs.hosting),
        current_task_id=task.id if task is not None else None,
        current_task_role=task.spec.role if task is not None else None,
        open_alarms=len(alarms_on_cell(inputs.alarms, cell.id)),
        highest_wax_severity=highest_severity(mine),
        lease_age_s=max((inputs.as_of - since).total_seconds(), 0.0) if since else None,
        warden_id=cell.warden_id,
        links=_links(cell, inputs.samples),
    )


def _current_task(cell: CellView, tasks: Sequence[Task]) -> Task | None:
    """Pick the Cell's running task, else the first task placed on it."""
    placed = [task for task in tasks if task.id in cell.current_tasks]
    running = [task for task in placed if task.status is TaskStatus.RUNNING]
    candidates = running or placed
    return candidates[0] if candidates else None


def _links(cell: CellView, samples: Sequence[TelemetrySampleView]) -> FleetLinks:
    """List the ids a row links through: the Cell, its Warden and every bee seen reporting."""
    bees: list[str] = []
    if cell.warden_id is not None:
        bees.append(cell.warden_id)
        # Each sub-bee that reported on its Warden's Heartbeat, in the Heartbeat's order.
        bees.extend(
            sample.worker_id
            for sample in samples
            if sample.warden_id == cell.warden_id and sample.worker_id is not None
        )
    return FleetLinks(cell_id=cell.id, attendant_principal=cell.warden_id, thought_principals=bees)
