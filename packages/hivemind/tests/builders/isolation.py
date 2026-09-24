"""Build Guard request and isolation test data: a report, a filed request, a decision, a hold.

Roadmap step 10.6a's tests (the Queen's side of a Guard request, and Cell isolation) all start from
a `hivemind.guard.GuardReport`: the rule that fired, the trail events it cites, the Cell, bees and
tasks it names, the action it recommends and how sure it is. `make_guard_report` builds a valid
one with sensible defaults (an isolate request at HIGH confidence under the shipped dire pattern),
so a test states only the fact under test; `make_guard_request`, `make_decision` and `make_hold`
build the Queen's table rows around one.

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

from hivemind.guard import GuardAction, GuardConfidence, GuardReport, new_guard_report_id
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.guard_requests import GuardBasis, GuardDecision, GuardRequest, PlacementHold
from waggle.clock import Clock, FakeClock
from waggle.ids import CellId, EventId, TaskId, new_cell_id, new_event_id, new_task_id

DIRE_RULE = "injection_then_denial"  # The shipped [guard] dire_patterns entry.
JUDGED_RULE = "out_of_scratch_burst"  # A rule no shipped dire pattern names: judged awake.

__all__ = [
    "DIRE_RULE",
    "JUDGED_RULE",
    "make_decision",
    "make_guard_report",
    "make_guard_request",
    "make_hold",
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
