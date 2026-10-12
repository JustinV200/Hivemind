"""Build one Cell's page: its bees, work, Forage, Cell Wax, Alarms and Capping at a glance.

A Cell page (roadmap 12.4) redraws everything about one Cell from the records the Hive keeps:
the Cell's own view (tier, Mask state, access level, mode, lease or Virtual status), its Warden
and where the Warden runs, each bee (the Warden and every sub-bee it listed) with its role, state,
current task and context gauge from its newest telemetry sample, whether an Exoskeleton is
attached, where its models come from, its placed tasks and their goals, the Forage its Warden
holds (grants used against issued, requests still waiting), its Cell Wax, its open Alarms and its
Capping activity. It carries ids, states and figures only: a bee's goal line, a task's title and
a note's text are the humans' words and stay behind ``observe:thoughts`` and C2, reached through
the ids this page hands out (``thought_principals``, the task ids and ``honey_folder``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built from
    gathered records by ``hivemind.entrance.reads.screens.cell_page``. Pure: calls into the
    sibling builders, the task, Cell Wax and grant records and pydantic.

Key invariants:
    - Pure: every input is handed in.
    - No field carries a bee's goal line, a task's words or a Cell Wax note's text.
    - A figure no store can fill yet (``last_patrol_at``, ``exoskeleton_attached`` before any
      attach was recorded) is null and documented as such, never filled in.

See Also:
    - hivemind.observation.api.fleet for the row this page expands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import Task
from hivemind.memory import CellWax, WaxSeverity, WaxState
from hivemind.observation.api.alarms import OpenAlarmView, alarms_on_cell
from hivemind.observation.api.capping import CappingView, cappings_on_cell
from hivemind.observation.api.fleet import WardenRuns, cell_status, live_wax, warden_runs
from hivemind.observation.api.hosting import HostingFacts, HostingView, hosting_view
from hivemind.observation.views.cells import CellView
from hivemind.observation.views.forage import GrantView
from hivemind.observation.views.frames import TelemetrySampleView
from hivemind.observation.views.tasks import TaskView, task_view
from hivemind.observation.views.wardens import WardenView
from hivemind.pheromone import PheromoneEvent
from waggle.messages.base import GrantIdField, TaskIdField
from waggle.messages.cell.wax import WaxOrigin

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

WARDEN_ROLE = "WARDEN"  # The role a Cell's supervisor is shown under in its bee list.
_ATTACHED = "cell.exoskeleton_attached"  # The trail edge that equips a Cell.
_DETACHED = "cell.exoskeleton_detached"  # The trail edge that returns it to a bare terminal.
_REQUESTED = "forage.requested"  # A Warden asked for more; open until answered.
_ANSWERS = frozenset({"forage.granted", "forage.denied"})  # What closes a request.

__all__ = [
    "WARDEN_ROLE",
    "BeeRow",
    "CellPageInputs",
    "CellPageView",
    "PendingRequestView",
    "WardenForageView",
    "WaxEntryView",
    "cell_page",
    "pending_requests",
]


class BeeRow(BaseModel):
    """One bee on the Cell: the Warden, or a sub-bee its Heartbeat listed."""

    model_config = _CONFIG

    id: str = Field(description="The Warden's or sub-bee's id.")
    role: str = Field(
        description="WARDEN, or the Worker role of its current task; UNKNOWN when the sub-bee "
        "has no task the Brood Chamber can name."
    )
    status: str = Field(description="Its Warden state or Worker state, as it reported.")
    task_id: TaskIdField | None = Field(description="What it is doing now: its current task.")
    context_tokens_used: int | None = Field(description="Tokens in its context; null unreported.")
    context_window: int | None = Field(description="Its bound model's context window.")
    context_fraction: float | None = Field(description="Used over window, 0..1, for the gauge.")
    spend_usd: float | None = Field(description="Its spend so far, from its telemetry.")


class WaxEntryView(BaseModel):
    """One Cell Wax note, without its text (observe)."""

    model_config = _CONFIG

    id: str = Field(description="The note's id.")
    severity: WaxSeverity = Field(description="NOTE, CAUTION or BLOCK.")
    state: WaxState = Field(description="PROPOSED or WRITTEN.")
    origin: WaxOrigin = Field(description="Who noticed it: a bee, a Patrol or the human.")
    proposer: str | None = Field(description="The proposing bee; null for the human's.")
    task_id: TaskIdField | None = Field(description="The task during which it was noticed.")
    proposed_at: datetime = Field(description="When it was proposed.")
    expires_at: datetime | None = Field(description="When it lapses; null for standing wax.")


class PendingRequestView(BaseModel):
    """A Forage request the Queen has not yet answered."""

    model_config = _CONFIG

    grant_id: GrantIdField = Field(description="The grant the request concerns.")
    request_kind: str | None = Field(description="SHARED_SEATS, SPEND, BINDING or SUB_BEES.")
    requested_at: datetime = Field(description="When it was asked.")


class WardenForageView(BaseModel):
    """The Forage the Cell's Warden holds: issued against used, and what it waits on."""

    model_config = _CONFIG

    grants: list[GrantView] = Field(description="Its live grants.")
    token_budget: int = Field(description="Tokens issued across them.")
    tokens_spent: int = Field(description="Tokens spent against them.")
    spend_budget_usd: float = Field(description="Spend issued across them, in US dollars.")
    spent_usd: float = Field(description="Spend charged to them.")
    pending_requests: list[PendingRequestView] = Field(description="Requests awaiting an answer.")


class CellPageView(BaseModel):
    """Everything one Cell's page shows (observe); words stay behind the ids it hands out."""

    model_config = _CONFIG

    as_of: datetime = Field(description="When the page was built.")
    cell: CellView = Field(description="The Cell, with its tier and Mask state.")
    status: str = Field(description="The headline status (as the Fleet shows it).")
    warden: WardenView | None = Field(description="Its Warden; null when none is attached.")
    warden_runs: WardenRuns | None = Field(description="hive_stand or on_cell; null unknown.")
    hosting: HostingView = Field(description="Where its bees' models come from.")
    lease_age_s: float | None = Field(description="Seconds since its open lease began.")
    exoskeleton_attached: bool | None = Field(
        description="From the newest attach or detach on the trail; null before any was recorded."
    )
    bees: list[BeeRow] = Field(description="The Warden, then each sub-bee.")
    tasks: list[TaskView] = Field(
        description="Its placed tasks (no words); goal_id names the goal."
    )
    forage: WardenForageView | None = Field(description="The Warden's Forage; null with no Warden.")
    last_patrol_at: datetime | None = Field(
        description="When its last Patrol ran; always null until roadmap phase 11 builds Patrols."
    )
    wax: list[WaxEntryView] = Field(description="Its Cell Wax, highest severity first.")
    alarms: list[OpenAlarmView] = Field(description="Its open Alarms.")
    capping: CappingView = Field(description="Capping activity on this Cell.")
    thought_principals: list[str] = Field(description="Bees whose thoughts can be opened.")
    honey_folder: str = Field(description="The Honey browser folder for what the Cell can see.")


@dataclass(frozen=True, slots=True)
class CellPageInputs:
    """The gathered records a Cell page is built from.

    Attributes:
        cell: The Cell's view.
        warden: Its Warden's view, if one is attached.
        tasks: The unfinished tasks placed on the Cell.
        samples: The newest telemetry samples of its Warden and sub-bees.
        grants: Its Warden's live grants.
        forage_events: ``forage.requested``, ``granted`` and ``denied`` events on those grants.
        wax: Its Cell Wax notes (PROPOSED and WRITTEN).
        alarms: Every open Alarm (placed by Cell here).
        capping: The Hive-wide Capping view (narrowed to the Cell here).
        hosting: The hosting plans and hosts.
        exoskeleton_edge: The kind of the Cell's newest ``cell.exoskeleton_*`` event, if any.
        lease_since: When its open lease began, if it has one.
        as_of: The instant the page is built for.
    """

    cell: CellView
    warden: WardenView | None
    tasks: Sequence[Task]
    samples: Sequence[TelemetrySampleView]
    grants: Sequence[GrantView]
    forage_events: Sequence[PheromoneEvent]
    wax: Sequence[CellWax]
    alarms: Sequence[OpenAlarmView]
    capping: CappingView
    hosting: HostingFacts
    exoskeleton_edge: str | None
    lease_since: datetime | None
    as_of: datetime


def cell_page(inputs: CellPageInputs) -> CellPageView:
    """Build one Cell's page.

    Args:
        inputs: The gathered records.

    Returns:
        The page.
    """
    cell, warden = inputs.cell, inputs.warden
    bees = _bees(inputs)
    return CellPageView(
        as_of=inputs.as_of,
        cell=cell,
        status=cell_status(cell, warden),
        warden=warden,
        warden_runs=warden_runs(cell),
        hosting=hosting_view(cell.id, inputs.hosting),
        lease_age_s=_age(inputs.lease_since, inputs.as_of),
        exoskeleton_attached=_exoskeleton(inputs.exoskeleton_edge),
        bees=bees,
        tasks=[task_view(task) for task in inputs.tasks],
        forage=_forage(inputs) if warden is not None else None,
        last_patrol_at=None,
        wax=_wax(inputs.wax, inputs.as_of),
        alarms=list(alarms_on_cell(inputs.alarms, cell.id)),
        capping=cappings_on_cell(inputs.capping, cell.id),
        thought_principals=[bee.id for bee in bees],
        honey_folder=f"/cells/{cell.id}",
    )


def pending_requests(events: Sequence[PheromoneEvent]) -> list[PendingRequestView]:
    """Find the Forage requests whose newest event is still the request.

    Args:
        events: ``forage.requested``, ``forage.granted`` and ``forage.denied`` events, any order.

    Returns:
        One entry per grant whose newest event is a request, oldest first.
    """
    newest: dict[str, PheromoneEvent] = {}
    # Oldest first, so the last event per grant is its newest.
    for event in sorted(events, key=lambda hop: hop.at):
        if event.kind == _REQUESTED or event.kind in _ANSWERS:
            newest[event.subject_id] = event
    kind = "request_kind"
    return [
        PendingRequestView(
            grant_id=event.subject_id,
            request_kind=value if isinstance(value := event.payload.get(kind), str) else None,
            requested_at=event.at,
        )
        for event in newest.values()
        if event.kind == _REQUESTED
    ]


def _bees(inputs: CellPageInputs) -> list[BeeRow]:
    """List the Warden and each sub-bee from the newest samples, the Warden first."""
    warden = inputs.warden
    if warden is None:
        return []
    roles: dict[str, str] = {task.id: task.spec.role.value for task in inputs.tasks}
    rows: list[BeeRow] = []
    for sample in inputs.samples:
        if sample.warden_id != warden.id:
            continue
        if sample.worker_id is None:
            rows.insert(0, _bee(warden.id, WARDEN_ROLE, _state(sample), sample))
            continue
        role = roles.get(sample.task_id or "", "UNKNOWN")
        rows.append(_bee(sample.worker_id, role, _state(sample), sample))
    # A Warden that has sent no Heartbeat yet still heads the list, with nothing to gauge.
    if not rows or rows[0].id != warden.id:
        state = warden.state.value if warden.state is not None else "UNKNOWN"
        rows.insert(0, _bee(warden.id, WARDEN_ROLE, state, None))
    return rows


def _state(sample: TelemetrySampleView) -> str:
    """Name a sample's state: the sub-bee's Worker state, else its Warden's state."""
    state = sample.worker_state or sample.warden_state
    return state.value if state is not None else "UNKNOWN"


def _bee(bee_id: str, role: str, status: str, sample: TelemetrySampleView | None) -> BeeRow:
    """Build one bee's row, its gauge from the sample when there is one."""
    used = sample.tokens_used if sample is not None else None
    window = sample.context_window if sample is not None else None
    return BeeRow(
        id=bee_id,
        role=role,
        status=status,
        task_id=sample.task_id if sample is not None else None,
        context_tokens_used=used,
        context_window=window,
        context_fraction=min(used / window, 1.0) if used is not None and window else None,
        spend_usd=sample.spend if sample is not None else None,
    )


def _forage(inputs: CellPageInputs) -> WardenForageView:
    """Total the Warden's grants and list the requests still waiting."""
    grants = list(inputs.grants)
    return WardenForageView(
        grants=grants,
        token_budget=sum(grant.token_budget for grant in grants),
        tokens_spent=sum(grant.tokens_spent for grant in grants),
        spend_budget_usd=sum(grant.spend_budget for grant in grants),
        spent_usd=sum(grant.spent for grant in grants),
        pending_requests=pending_requests(inputs.forage_events),
    )


def _wax(wax: Sequence[CellWax], as_of: datetime) -> list[WaxEntryView]:
    """List the Cell's proposed and live written wax, highest severity first, newest first."""
    keep = [
        *live_wax(wax, as_of),
        *(note for note in wax if note.state is WaxState.PROPOSED),
    ]
    rank = list(WaxSeverity)
    keep.sort(key=lambda note: (-rank.index(note.severity), -note.proposed_at.timestamp()))
    return [
        WaxEntryView(
            id=note.id,
            severity=note.severity,
            state=note.state,
            origin=note.origin,
            proposer=note.proposer,
            task_id=note.task_id,
            proposed_at=note.proposed_at,
            expires_at=note.expires_at,
        )
        for note in keep
    ]


def _exoskeleton(edge: str | None) -> bool | None:
    """Read the newest attach or detach edge: attached, detached, or never recorded."""
    if edge == _ATTACHED:
        return True
    return False if edge == _DETACHED else None


def _age(since: datetime | None, now: datetime) -> float | None:
    """Return seconds from ``since`` to ``now``, or None without a start."""
    return max((now - since).total_seconds(), 0.0) if since is not None else None
