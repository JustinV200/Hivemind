"""Carry out the Queen's decision on a Guard request: isolate, Sting Cut, quarantine, or dismiss.

Roadmap step 10.6a (ADR-0043). The decision is already on the trail (`queen.decided`) when this
runs. ISOLATE_CELL goes through the one isolation path (`hivemind.queen.isolation.isolate_cell`)
as the Queen's order, citing the report and its evidence; when the `isolation` point refuses her
because the Cell is the Hive Stand's own, the Hive Stand fallback runs instead (`.hive_stand`).
STING_CUT (roadmap step 13.4a) isolates exactly so, and then, once an isolation stands, runs the
one Sting Cut path as her own cut, which keeps its own conditions (her hourly cap, evidence first,
never the Hive Stand); a cut it refuses leaves the Cell isolated, and the outcome word says why.
QUARANTINE_BEE orders a quarantine of every implicated task through the 10.6c order. DISMISS does
nothing: the report stays on the trail as the Guard Bee's `guard.alert`, and the decision row
says it was dismissed. Whatever cannot be carried out (no Cell known, its Warden gone, the point
refusing for another reason) is said in the outcome word, never raised: a request is always
settled, so a request that cannot be acted on can never wedge her tick.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package. Called by `.decide`. Calls into `hivemind.guard`,
    `hivemind.queen.autopilot` (QueenAction), `hivemind.queen.errors`, `hivemind.queen.isolation`,
    `hivemind.queen.sting_cut` and the sub-package's own modules only.

Key invariants:
    - Every isolation it orders is `ordered_by=QUEEN` and names the report and its decision.
    - Returns an outcome for every action; raises for none of the four.
    - Every cut it orders is `cut_by=QUEEN` and follows the isolation it ordered or found.

See Also:
    - hivemind.queen.isolation.path for the one isolation path.
"""

from __future__ import annotations

from hivemind.guard import GuardReport
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.errors import UnknownCellError
from hivemind.queen.guard_requests.decision.hive_stand import hive_stand_fallback
from hivemind.queen.guard_requests.decision.outcome import ActOutcome
from hivemind.queen.guard_requests.decision.quarantine import quarantine_tasks
from hivemind.queen.guard_requests.decision.target import implicated_tasks
from hivemind.queen.isolation import (
    IsolationOrder,
    IsolationRefusal,
    IsolationSite,
    Isolator,
    isolate_cell,
)
from hivemind.queen.sting_cut import Cutter, StingCutOrder, sting_cut_cell
from waggle.ids import CellId, EventId

# The isolation outcomes a Sting Cut may follow: an isolation now, or one standing already.
_CUTTABLE = frozenset({"isolated", "already_isolated"})

__all__ = ["carry_out"]


async def carry_out(
    site: IsolationSite,
    report: GuardReport,
    action: QueenAction,
    target: CellId | None,
    decided_event_id: EventId,
) -> ActOutcome:
    """Carry out `action` on `report`'s target; say what came of it.

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        report: The Guard request's report.
        action: ISOLATE_CELL, STING_CUT, QUARANTINE_BEE or DISMISS.
        target: The Cell an isolation cuts off; None when none is known.
        decided_event_id: Her `queen.decided` event for this decision.

    Returns:
        What came of it, for the request's row and the Alarm rule.
    """
    if action is QueenAction.ISOLATE_CELL:
        return await _isolate(site, report, target, decided_event_id)
    if action is QueenAction.STING_CUT:
        isolated = await _isolate(site, report, target, decided_event_id)
        if target is None or isolated.outcome not in _CUTTABLE:
            return isolated  # Not isolated (the Hive Stand's fallback, no Cell): never cut.
        return await _cut(site, report, target, decided_event_id, isolated)
    if action is QueenAction.QUARANTINE_BEE:
        tasks = await implicated_tasks(site.deps, report, None)
        ordered = await quarantine_tasks(site, report, tasks)
        word = "quarantine_ordered" if ordered else "quarantine_not_sent"
        return ActOutcome(outcome=word, acted=bool(ordered), alarmed=False)
    return ActOutcome(outcome="dismissed", acted=False, alarmed=False)


async def _isolate(
    site: IsolationSite, report: GuardReport, target: CellId | None, decided_event_id: EventId
) -> ActOutcome:
    """Isolate the target through the one path; fall back on the Hive Stand."""
    if target is None:
        return ActOutcome(outcome="no_target_cell", acted=False, alarmed=False)
    order = IsolationOrder(
        cell_id=target,
        ordered_by=Isolator.QUEEN,
        reason=f"Guard report {report.id} ({report.rule}, {report.confidence.value}).",
        report_id=report.id,
        evidence=report.event_ids,
        decision_event_id=decided_event_id,
    )
    try:
        outcome = await isolate_cell(site, order)
    except UnknownCellError:
        return ActOutcome(outcome="cell_not_attached", acted=False, alarmed=False)
    if outcome.refusal is IsolationRefusal.HIVE_STAND:
        # ADR-0043: only the human isolates the Hive Stand; the Queen does what she may there.
        return await hive_stand_fallback(site, report, target, decided_event_id)
    if outcome.refusal is not None:
        return ActOutcome(outcome="isolation_refused", acted=False, alarmed=False)
    if outcome.already_isolated:
        return ActOutcome(outcome="already_isolated", acted=False, alarmed=False)
    return ActOutcome(outcome="isolated", acted=True, alarmed=True)


async def _cut(
    site: IsolationSite,
    report: GuardReport,
    target: CellId,
    decided_event_id: EventId,
    isolated: ActOutcome,
) -> ActOutcome:
    """Sting Cut the isolated target as the Queen's own cut; a refusal leaves it isolated."""
    order = StingCutOrder(
        cell_id=target,
        cut_by=Cutter.QUEEN,
        reason=f"Guard report {report.id} ({report.rule}, {report.confidence.value}).",
        report_id=report.id,
        evidence=report.event_ids,
        decision_event_id=decided_event_id,
    )
    try:
        outcome = await sting_cut_cell(site, order)
    except UnknownCellError:
        return ActOutcome(outcome="isolated_cut_unreachable", acted=True, alarmed=isolated.alarmed)
    if outcome.refusal is not None:
        # The isolation stands and was shown; the decision row says why it went no further.
        word = f"isolated_cut_refused_{outcome.refusal.value}"
        return ActOutcome(outcome=word, acted=True, alarmed=isolated.alarmed)
    word = "already_cut" if outcome.already_cut else "sting_cut"
    return ActOutcome(outcome=word, acted=True, alarmed=True)
