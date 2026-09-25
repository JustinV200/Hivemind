"""Build Guard request and isolation test data: a report, a filed request, a decision, a hold.

Roadmap step 10.6a's tests (the Queen's side of a Guard request, and Cell isolation) all start from
a `hivemind.guard.GuardReport`: the rule that fired, the trail events it cites, the Cell, bees and
tasks it names, the action it recommends and how sure it is. `make_guard_report` builds a valid
one with sensible defaults (an isolate request at HIGH confidence under the shipped dire pattern),
so a test states only the fact under test; `make_guard_request`, `make_decision` and `make_hold`
build the Queen's table rows around one. For isolation itself: `tracked_virtual_cell` provisions
one Virtual Cell on the fake backend through a real `CellLifecycle` (its `LifecycleEgress` is the
seam the path cuts), `hive_stand_cell` is the Hive Stand's own Cell, `place_running` puts a task
RUNNING on a link's Cell exactly as the dispatcher leaves it (with its `queen.assigned` row), and
`isolation_site` / `queen_order` build what the one path takes. `night_veil_cell` and `veil_cell`
put a Queen behind the Night Veil boundary with one living Night Veil Cell's segment held, where
everything about that Cell is recorded (codingrules 12).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/guard_requests, tests/unit/queen/isolation and the contract suite.

Key invariants:
    - Every value built here passes the models' own validators.

See Also:
    - hivemind.guard.report for GuardReport.
    - hivemind.queen.guard_requests.model for the rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from builders.cells import make_cell, make_identity
from builders.forage import make_capacity
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CellKind, CombShieldLevel
from hivemind.guard import GuardAction, GuardConfidence, GuardReport, new_guard_report_id
from hivemind.hive import (
    BackendRegistry,
    CellLifecycle,
    FakeCellBackend,
    LifecycleEgress,
    VirtualCellSpec,
)
from hivemind.pheromone import EphemeralSegments, VeiledTrail
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.guard_requests import GuardBasis, GuardDecision, GuardRequest, PlacementHold
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.isolation import IsolationOrder, IsolationSite, Isolator
from hivemind.queen.trail import record_event
from waggle.clock import Clock, FakeClock
from waggle.ids import CellId, EventId, TaskId, new_cell_id, new_event_id, new_hive_id, new_task_id

DIRE_RULE = "injection_then_denial"  # The shipped [guard] dire_patterns entry.
JUDGED_RULE = "out_of_scratch_burst"  # A rule no shipped dire pattern names: judged awake.

HIVE_STAND_SOURCE = "hive_stand"  # The Hive Stand's Cell source, as placement reads it.

__all__ = [
    "DIRE_RULE",
    "HIVE_STAND_SOURCE",
    "JUDGED_RULE",
    "hive_stand_cell",
    "isolation_site",
    "make_decision",
    "make_guard_report",
    "make_guard_request",
    "make_hold",
    "night_veil_cell",
    "place_running",
    "queen_order",
    "tracked_virtual_cell",
    "veil_cell",
]


def make_guard_report(
    clock: Clock | None = None,
    *,
    cell_id: CellId | None = None,
    event_ids: Sequence[EventId] = (),
    **overrides: object,
) -> GuardReport:
    """Build a valid Guard report: an isolate request under the dire rule, unless overridden.

    Args:
        clock: Source of every id and the filing time; a fresh FakeClock when omitted.
        cell_id: The Cell the report names; a fresh id when omitted.
        event_ids: The trail events it cites; one fresh id when empty.
        **overrides: Any other GuardReport field (rule, bee_ids, task_ids, recommended, ...).

    Returns:
        The report.
    """
    active = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_guard_report_id(active),
        "rule": DIRE_RULE,
        "event_ids": tuple(event_ids) or (new_event_id(active),),
        "cell_id": cell_id if cell_id is not None else new_cell_id(active),
        "recommended": GuardAction.ISOLATE_CELL,
        "confidence": GuardConfidence.HIGH,
        "filed_at": active.now(),
        "summary": "1 injection-suspected event, then 1 denial in the same episode.",
    }
    fields.update(overrides)
    return GuardReport.model_validate(fields)


def make_guard_request(
    clock: Clock | None = None, report: GuardReport | None = None
) -> GuardRequest:
    """Build an undecided request around `report` (a fresh `make_guard_report` when omitted).

    Args:
        clock: Source of every id and the filing time; a fresh FakeClock when omitted.
        report: The report filed; a default isolate request under the dire rule when omitted.

    Returns:
        The request, filed now.
    """
    active = clock if clock is not None else FakeClock()
    filed = report if report is not None else make_guard_report(active)
    return GuardRequest(report=filed, filed_at=active.now())


def make_decision(
    clock: Clock | None = None,
    action: QueenAction = QueenAction.ISOLATE_CELL,
    basis: GuardBasis = GuardBasis.RULE,
) -> GuardDecision:
    """Build a decision on a request, recorded by a fresh `queen.decided` event id.

    Args:
        clock: Source of the event id and the time; a fresh FakeClock when omitted.
        action: What was decided.
        basis: What it rested on.

    Returns:
        The decision.
    """
    active = clock if clock is not None else FakeClock()
    return GuardDecision(
        action=action,
        basis=basis,
        event_id=new_event_id(active),
        decided_at=active.now(),
        outcome="isolated",
    )


def make_hold(
    request: GuardRequest, clock: Clock | None = None, goal_ids: Sequence[TaskId] = ()
) -> PlacementHold:
    """Build an active hold for `request`'s report, on its Cell, for `goal_ids`.

    Args:
        request: The request whose decision leaves the hold.
        clock: Source of ids and the time; a fresh FakeClock when omitted.
        goal_ids: The goals held; one fresh id when empty.

    Returns:
        The hold, not yet released.
    """
    active = clock if clock is not None else FakeClock()
    cell_id = request.report.cell_id or new_cell_id(active)
    return PlacementHold(
        report_id=request.id,
        cell_id=cell_id,
        goal_ids=tuple(goal_ids) or (new_task_id(active),),
        held_at=active.now(),
    )


async def tracked_virtual_cell(
    clock: FakeClock,
) -> tuple[LifecycleEgress, FakeCellBackend, Cell]:
    """Provision one Virtual Cell on the fake backend through a real lifecycle.

    Args:
        clock: Shared by the lifecycle and the backend.

    Returns:
        The lifecycle's egress seam, the backend (to read `egress_is_cut` back) and the Cell.
    """
    backend = FakeCellBackend(clock)
    registry = BackendRegistry()
    registry.register("fake", lambda: backend)
    lifecycle = CellLifecycle(registry, MemoryPheromoneTrail(clock), clock, make_identity(clock))
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=2.0,
        memory_bytes=2 * 1024**3,
        disk_bytes=10 * 1024**3,
        capacity=make_capacity(),
        hive_id=new_hive_id(clock),
    )
    cell = await lifecycle.provision(spec, "fake")
    return LifecycleEgress(lifecycle), backend, cell


def hive_stand_cell(clock: Clock | None = None) -> Cell:
    """Build the Hive Stand's own Cell: a Real Cell whose source placement reads as the Stand.

    Args:
        clock: Source of the Cell's id; a fresh FakeClock when omitted.

    Returns:
        The Cell.
    """
    return make_cell(kind=CellKind.REAL, clock=clock, source=HIVE_STAND_SOURCE)


def night_veil_cell(clock: Clock | None = None) -> Cell:
    """Build a living Night Veil Cell, as its Warden's link carries it once attached.

    Args:
        clock: Source of the Cell's id; a fresh FakeClock when omitted.

    Returns:
        A Virtual Cell at NIGHT_VEIL.
    """
    return make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.NIGHT_VEIL)


def veil_cell(deps: QueenDeps, cell_id: CellId) -> tuple[QueenDeps, EphemeralSegments]:
    """Put `deps` behind the Night Veil boundary with `cell_id`'s segment held.

    Args:
        deps: The Queen's collaborators over a plain (durable) trail.
        cell_id: The living Night Veil Cell.

    Returns:
        The same collaborators recording through a `VeiledTrail` over their trail, and the
        segments behind it, where every record about the Cell now waits.
    """
    segments = EphemeralSegments(deps.clock)
    segments.open(cell_id)
    return replace(deps, trail=VeiledTrail(deps.trail, segments)), segments


async def place_running(deps: QueenDeps, link: WardenLink) -> Task:
    """Put a fresh one-task goal RUNNING on `link`'s Cell, as the dispatcher leaves it.

    Args:
        deps: The Queen's collaborators; the chamber and trail are written.
        link: The attached Warden whose Cell the task is placed on.

    Returns:
        The task, RUNNING, with the `queen.assigned` row the dispatcher records.
    """
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    tier = link.cell.comb_shield
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "Placed.", bound_tier=tier)
    await record_event(
        deps, "queen.assigned", task.id, cell_id=link.cell.id, warden_id=link.warden_id
    )
    return await deps.chamber.start(task.id)


def isolation_site(deps: QueenDeps, *links: WardenLink) -> IsolationSite:
    """Build what an isolation runs against: `deps`, the attached `links`, a fresh human inbox.

    Args:
        deps: The Queen's collaborators.
        *links: The Wardens attached now.

    Returns:
        The site.
    """
    return IsolationSite(deps=deps, wardens=links, human_inbox=HumanInbox())


def queen_order(
    cell_id: CellId, report: GuardReport | None = None, ordered_by: Isolator = Isolator.QUEEN
) -> IsolationOrder:
    """Build an isolation order for `cell_id`, citing `report` and its evidence when given.

    Args:
        cell_id: The Cell to isolate.
        report: The Guard report the order answers, if any.
        ordered_by: The Queen by default; the human for their own lever.

    Returns:
        The order.
    """
    return IsolationOrder(
        cell_id=cell_id,
        ordered_by=ordered_by,
        reason="Isolated for a test.",
        report_id=report.id if report is not None else None,
        evidence=report.event_ids if report is not None else (),
    )
