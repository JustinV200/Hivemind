"""Fall back on the Hive Stand, where only the human isolates: quarantine, hold placements, alarm.

ADR-0035: the Hive Stand's own lease (the machine the Hive runs on) is isolated only by the human.
When the Queen decides to isolate it, the `isolation` enforcement point refuses her (a
`guard.denied` row, rule `guard.scope.hive_stand`), and her decision falls back to the three things
she may do there (roadmap step 10.6a): quarantine every implicated bee through the 10.6c order;
hold new placements of the implicated goals off the Hive Stand, as a `PlacementHold` row her
dispatcher reads as a BLOCK on that Cell for those goals alone (placement data, not a special case
in `decide`); and raise a CRITICAL SECURITY Alarm with the report, pushed to every device, so the
human can isolate the Hive Stand themselves (citing the report) or lift the hold.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package. Called by `.act` when the isolation point refused the
    Queen on the Hive Stand. Calls into `hivemind.guard` (GuardReport), `hivemind.queen.isolation`
    (IsolationSite, alert_human), `hivemind.supervision` (AlarmSeverity), the sub-package's own
    target, quarantine and outcome, the package's model, and waggle only.

Key invariants:
    - The hold names only goals of tasks placed on the Hive Stand, and at most MAX_HELD_GOALS.
    - The Alarm is always CRITICAL and always names the report.

See Also:
    - hivemind.queen.dispatcher.snapshot for how placement reads a hold.
    - hivemind.queen.isolation.lift for the human's lift, which releases it.
"""

from __future__ import annotations

from hivemind.guard import GuardReport
from hivemind.queen.guard_requests.decision.outcome import ActOutcome
from hivemind.queen.guard_requests.decision.quarantine import quarantine_tasks
from hivemind.queen.guard_requests.decision.target import implicated_tasks
from hivemind.queen.guard_requests.model import MAX_HELD_GOALS, PlacementHold
from hivemind.queen.isolation import IsolationSite, alert_human
from hivemind.supervision import AlarmSeverity
from waggle.ids import CellId, EventId

FALLBACK_OUTCOME = "hive_stand_fallback"  # The decision's outcome word for this path.

__all__ = ["FALLBACK_OUTCOME", "hive_stand_fallback"]


async def hive_stand_fallback(
    site: IsolationSite, report: GuardReport, cell_id: CellId, decided_event_id: EventId
) -> ActOutcome:
    """Quarantine the implicated bees, hold their goals off the Hive Stand, and alarm the human.

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        report: The Guard request's report.
        cell_id: The Hive Stand's Cell, which the Queen may not isolate.
        decided_event_id: Her `queen.decided` event, which the Alarm points back to.

    Returns:
        The outcome, with the hold for her decision's row (None when no goal was implicated).
    """
    tasks = await implicated_tasks(site.deps, report, cell_id)
    quarantined = await quarantine_tasks(site, report, tasks)
    goals = tuple(dict.fromkeys(task.goal_id for task in tasks))[:MAX_HELD_GOALS]
    hold = (
        PlacementHold(
            report_id=report.id, cell_id=cell_id, goal_ids=goals, held_at=site.deps.clock.now()
        )
        if goals
        else None
    )
    detail = (
        f"Guard report {report.id} ({report.rule}) is about the Hive Stand ({cell_id}), which "
        f"only you can isolate. The Queen quarantined {len(quarantined)} task(s) and holds "
        f"{len(goals)} goal(s) off the Hive Stand until you isolate it or lift the hold."
    )
    await alert_human(site, AlarmSeverity.CRITICAL, detail, cell_id, decided_event_id)
    return ActOutcome(outcome=FALLBACK_OUTCOME, acted=True, alarmed=True, hold=hold)
