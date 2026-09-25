"""Decide one Guard request on the Queen's tick: rule, judgement or fallback, then act and stamp.

Roadmap step 10.6a (ADR-0043). Every undecided request is a GUARD_REQUEST item in the Queen's
inbox, ordered above every Alarm and every human message; her tick hands each one here. The
decision rests on one of three bases: the autopilot rule (a report whose rule is one of
`[guard] dire_patterns` isolates without a model), one awake episode with the report's facts
attached, or the fallback when no episode can decide (isolate, which only removes access). Then:

1. `queen.decided` is recorded with the report id, the action and its basis, before anything is
   done, so no Guard request ever isolates without the Queen's decision on the trail.
2. The decision is carried out (`.act`): the one isolation path, the Hive Stand's fallback, a
   quarantine order, or nothing.
3. The request's row is stamped with the decision, what came of it and any placement hold, so it
   is never decided again. A crash between 1 and 3 decides it again after the restart; every
   action is idempotent (an isolation that stands is not redone).
4. A report she acted on, or one at CRITICAL confidence, reaches the human as a SECURITY Alarm,
   pushed to every device, naming the report, unless acting already raised one (an isolation
   always does, and so does the Hive Stand's fallback).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package. Called by `hivemind.queen.queen`'s tick for every
    GUARD_REQUEST item. Calls into `hivemind.common.logging`, `hivemind.guard`,
    `hivemind.queen.autopilot`, `hivemind.queen.isolation` (IsolationSite, alert_human),
    `hivemind.queen.trail`, `hivemind.supervision`, the package's model and the sub-package's own
    modules only.

Key invariants:
    - `queen.decided` for a request precedes every effect of its decision on the trail.
    - A request already decided is never decided again.

See Also:
    - hivemind.queen.autopilot.guard for the rule; `.judge` for the episode; `.act` for the rest.
    - docs/guard/isolation.md for the request path end to end.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from hivemind.guard import GuardConfidence, GuardReport
from hivemind.queen.autopilot import QueenAction, decide_guard_request
from hivemind.queen.guard_requests.decision.act import carry_out
from hivemind.queen.guard_requests.decision.judge import judge_guard_request
from hivemind.queen.guard_requests.decision.outcome import ActOutcome
from hivemind.queen.guard_requests.decision.target import target_cell
from hivemind.queen.guard_requests.model import GuardBasis, GuardDecision, GuardRequest
from hivemind.queen.guard_requests.show import SecurityAlert
from hivemind.queen.isolation import IsolationSite, alert_human
from hivemind.queen.trail import queen_event
from hivemind.supervision import AlarmSeverity
from hivemind.supervision.attendant import InboxItem
from waggle.ids import CellId, EventId

DECIDED_KIND = "queen.decided"  # Her decision record, before any effect of it.

log = get_logger(__name__)

__all__ = ["DECIDED_KIND", "decide_guard_item"]


async def decide_guard_item(site: IsolationSite, item: InboxItem) -> GuardRequest | None:
    """Decide the Guard request `item` carries, act on it, stamp it and tell the human.

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        item: One GUARD_REQUEST inbox item; its id is the report's.

    Returns:
        The request as stamped; the stored row unchanged when it was already decided; None when
        no request carries that id.
    """
    deps = site.deps
    request = await deps.guard.requests.get(item.id)
    if request is None or not request.is_pending:
        return request  # Decided already (a second item for it in one tick): never twice.
    report = request.report
    target = await target_cell(deps, report)
    action = decide_guard_request(report, deps.guard.dire_patterns, target is not None)
    basis = GuardBasis.RULE
    if action is QueenAction.NEEDS_JUDGEMENT:
        action, basis = await judge_guard_request(site, report, target)
    decided = queen_event(
        deps,
        DECIDED_KIND,
        target or deps.identity.hive_id,
        action=action.value,
        basis=basis.value,
        report_id=report.id,
        rule=report.rule,
    )
    await deps.trail.record(decided)
    acted = await carry_out(site, report, action, target, decided.id)
    decision = GuardDecision(
        action=action,
        basis=basis,
        event_id=decided.id,
        decided_at=decided.at,
        outcome=acted.outcome,
    )
    stamped = await deps.guard.requests.decide(report.id, decision, acted.hold)
    await _tell_human(site, report, decision, acted, target)
    log.info(
        "queen.guard_request_decided",
        report_id=report.id,
        action=action.value,
        basis=basis.value,
        outcome=acted.outcome,
    )
    return stamped


async def _tell_human(
    site: IsolationSite,
    report: GuardReport,
    decision: GuardDecision,
    acted: ActOutcome,
    target: CellId | None,
) -> None:
    """Raise the SECURITY Alarm for an acted-on or CRITICAL report, unless one already went."""
    critical = report.confidence is GuardConfidence.CRITICAL
    if acted.alarmed or not (acted.acted or critical):
        return
    severity = AlarmSeverity.CRITICAL if critical else AlarmSeverity.WARNING
    detail = (
        f"Guard report {report.id} ({report.rule}, {report.confidence.value} confidence): the "
        f"Queen decided {decision.action.value} by {decision.basis.value}; {acted.outcome}."
    )
    alert = SecurityAlert(
        severity=severity,
        detail=detail,
        cell_id=target,
        event_id=EventId(decision.event_id),
        report_id=report.id,  # Shown once: a report already shown is not shown again.
    )
    await alert_human(site, alert)
