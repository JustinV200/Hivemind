"""Show the human a SECURITY Alarm about a Guard report, or an isolation, at most once per report.

ADR-0043: a report the Queen acts on, or one at CRITICAL confidence, reaches the human as a
SECURITY Alarm pushed to every device. Several paths can reach the human about the same report:
the Guard Bee's `report_to_human` for a CRITICAL finding, the Queen's decision on a request, the
isolation she carries out on it, the Hive Stand's fallback. `show_alert` is the one way all of
them do it, and it shows each report once. Every Alarm about a report carries the same id,
derived from the report's own (`report_alarm_id`: the report's ULID under the `alarm_` prefix),
and an Alarm whose `alarm.escalated` row is already on the trail is not raised again. That row is
durable (the Hive's own SQLite trail), so a restart never shows a report twice, and a lock in
`GuardDeps` keeps two concurrent callers from both passing the check. An alert about no report
(the Queen's own ISOLATE policy row, the human's isolation citing nothing) gets a fresh id. The
Alarm is raised by the Hive itself and escalated straight to the chain's last hop
(`hivemind.queen.chat.escalate_alarm`: the human's inbox, `alarm.escalated`, then `post_alarm`,
which appends it to the chat and pushes it to every enrolled device).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Called by `.door` (report_to_human), `hivemind.queen.isolation`
    (an isolation's Alarm) and `.decision` (a decision's). Calls into `hivemind.cell`,
    `hivemind.guard` (GuardReportId), `hivemind.pheromone` (TrailQuery), `hivemind.queen.chat`
    (escalate_alarm), `hivemind.supervision` (Alarm) and waggle only; `QueenDeps` and
    `HumanInbox` only for their types.

Key invariants:
    - Every Alarm here is SECURITY, HANDLING, at clearance C1, originated by the Hive.
    - At most one Alarm per report id, ever: the trail's `alarm.escalated` row is the proof.

See Also:
    - hivemind.queen.chat.post for escalate_alarm and post_alarm.
    - docs/guard/isolation.md, "What the human sees".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.guard import GuardReportId
from hivemind.guard.report import GUARD_REPORT_ID_PREFIX
from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import escalate_alarm
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState
from waggle.ids import AlarmId, CellId, EventId, new_alarm_id
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.supervision import AlarmContext

if TYPE_CHECKING:
    # Only for the type hints: hivemind.queen.deps imports this package for GuardDeps.
    from hivemind.queen.deps import QueenDeps
    from hivemind.queen.human_inbox import HumanInbox

ALARM_ID_PREFIX = "alarm_"  # waggle's IdKind.ALARM prefix; a report's Alarm reuses its ULID.
ESCALATED_KIND = "alarm.escalated"  # The row every Alarm that reached the human carries.

__all__ = [
    "ALARM_ID_PREFIX",
    "ESCALATED_KIND",
    "SecurityAlert",
    "report_alarm_id",
    "show_alert",
    "was_shown",
]


@dataclass(frozen=True, slots=True)
class SecurityAlert:
    """One thing to tell the human: how bad, what happened, and what it is about.

    Attributes:
        severity: CRITICAL for an isolation, the Hive Stand's fallback or a CRITICAL report.
        detail: One sentence naming ids only (a report's own rule-written summary may follow).
        cell_id: The Cell it is about, when it is about one.
        event_id: The trail event that explains it (`cell.isolated`, `queen.decided`, ...).
        report_id: The Guard report it is about; None for an alert about no report.
    """

    severity: AlarmSeverity
    detail: str
    cell_id: CellId | None = None
    event_id: EventId | None = None
    report_id: GuardReportId | None = None


def report_alarm_id(report_id: GuardReportId) -> AlarmId:
    """Return the one Alarm id every showing of `report_id` carries.

    Args:
        report_id: A `guardrep_<ULID>` report id.

    Returns:
        `alarm_<the same ULID>`: unique per report, and the same on every path and every restart.
    """
    return AlarmId(f"{ALARM_ID_PREFIX}{report_id.removeprefix(GUARD_REPORT_ID_PREFIX)}")


async def was_shown(deps: QueenDeps, report_id: GuardReportId) -> bool:
    """Return whether an Alarm about `report_id` already reached the human.

    Args:
        deps: The Queen's collaborators; `trail` is read.
        report_id: The Guard report.

    Returns:
        True once its `alarm.escalated` row is on the trail.
    """
    query = TrailQuery(kind=ESCALATED_KIND, subject_id=report_alarm_id(report_id), limit=1)
    return bool(await deps.trail.query(query))


async def show_alert(deps: QueenDeps, human_inbox: HumanInbox, alert: SecurityAlert) -> bool:
    """Raise `alert` at the human as a SECURITY Alarm, unless its report was shown already.

    Args:
        deps: The Queen's collaborators.
        human_inbox: Her inbox of Alarms waiting on the human.
        alert: What to tell the human.

    Returns:
        True when this call showed it; False when an Alarm about the same report already had.
    """
    # One check-then-raise at a time: the Guard Bee's call and her tick can race on one report.
    async with deps.guard.show_lock:
        if alert.report_id is not None and await was_shown(deps, alert.report_id):
            return False
        alarm_id = (
            report_alarm_id(alert.report_id)
            if alert.report_id is not None
            else new_alarm_id(deps.clock)
        )
        await escalate_alarm(deps, human_inbox, _alarm(deps, alarm_id, alert))
    return True


def _alarm(deps: QueenDeps, alarm_id: AlarmId, alert: SecurityAlert) -> Alarm:
    """Build the Hive's own SECURITY Alarm for `alert`, HANDLING, ids-only at C1."""
    return Alarm(
        id=alarm_id,
        kind=AlarmKind.SECURITY,
        severity=alert.severity,
        origin=deps.identity.hive_id,  # The Hive itself raised it; no bee sits above the Queen.
        attempts=0,
        context=AlarmContext(
            task_id=None,
            cell_id=alert.cell_id,
            worker_id=None,
            event_id=alert.event_id,
            handoff=None,
        ),
        detail=alert.detail[:MAX_REASON_CHARS],
        clearance=HoneyClearance.C1,
        raised_at=deps.clock.now(),
        state=AlarmState.HANDLING,
    )
