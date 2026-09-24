"""Define IsolationSite and alert_human: where an isolation runs, and how the human hears of it.

An isolation (roadmap step 10.6a, ADR-0035) needs three things only the running Queen holds
together: her collaborators, the Wardens attached to her right now (the isolated Cell's own
Warden relays the pause and hears of the revoked grant), and her inbox of Alarms waiting on the
human. `IsolationSite` bundles them for the one path, the lift and the Queen's decision on a Guard
request. `alert_human` is how any of them tells the human: a SECURITY Alarm raised by the Hive
itself, escalated straight to the chain's last hop (`hivemind.queen.chat.escalate_alarm`: the
human's inbox, `alarm.escalated` on the trail, then `post_alarm`, which pushes it to every enrolled
device), with the Cell and the event that explains it in the Alarm's context and ids only in its
detail.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Built by `hivemind.queen.isolation.door` and the Queen's Guard request
    decision. Calls into `hivemind.cell` (HoneyClearance), `hivemind.queen.chat`
    (escalate_alarm), `hivemind.supervision` (Alarm) and waggle only; `QueenDeps`, `WardenLink`
    and `HumanInbox` only for their types.

Key invariants:
    - Every Alarm it raises is SECURITY, HANDLING, at clearance C1, originated by the Hive.
    - Its detail names ids only, never content (codingrules section 12).

See Also:
    - hivemind.queen.chat.post for escalate_alarm and post_alarm.
    - hivemind.entrance.runtime.entrance for the same Alarm shape raised by the Hive Entrance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.queen.chat import escalate_alarm
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState
from waggle.ids import AlarmId, CellId, EventId, new_alarm_id
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.supervision import AlarmContext

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink
    from hivemind.queen.human_inbox import HumanInbox

__all__ = ["IsolationSite", "alert_human"]


@dataclass(frozen=True, slots=True)
class IsolationSite:
    """What an isolation runs against: the Queen's collaborators, Wardens and human inbox.

    Attributes:
        deps: The Queen's collaborators.
        wardens: Every Warden attached to her now, in attachment order.
        human_inbox: Her inbox of Alarms waiting on the human.
    """

    deps: QueenDeps
    wardens: Sequence[WardenLink]
    human_inbox: HumanInbox

    def link_for(self, cell_id: CellId) -> WardenLink | None:
        """Return the attached Warden that runs `cell_id`, or None.

        Args:
            cell_id: The Cell.

        Returns:
            Its Warden's link, when one is attached.
        """
        return next((link for link in self.wardens if link.cell.id == cell_id), None)


async def alert_human(
    site: IsolationSite,
    severity: AlarmSeverity,
    detail: str,
    cell_id: CellId | None,
    event_id: EventId | None,
) -> AlarmId:
    """Raise a SECURITY Alarm at the human, pushed to every device, and return its id.

    Args:
        site: Where the isolation or decision ran.
        severity: CRITICAL for an isolation or the Hive Stand's fallback; lower for a dismissal.
        detail: One sentence naming ids only: the Guard report, the Cell, the action.
        cell_id: The Cell it is about, when it is about one.
        event_id: The trail event that explains it (`cell.isolated`, or `queen.decided`).

    Returns:
        The Alarm's id, which the human acknowledges it by.
    """
    deps = site.deps
    alarm = Alarm(
        id=new_alarm_id(deps.clock),
        kind=AlarmKind.SECURITY,
        severity=severity,
        origin=deps.identity.hive_id,  # The Hive itself raised it; no bee sits above the Queen.
        attempts=0,
        context=AlarmContext(
            task_id=None, cell_id=cell_id, worker_id=None, event_id=event_id, handoff=None
        ),
        detail=detail[:MAX_REASON_CHARS],
        clearance=HoneyClearance.C1,
        raised_at=deps.clock.now(),
        state=AlarmState.HANDLING,
    )
    await escalate_alarm(deps, site.human_inbox, alarm)
    return alarm.id
