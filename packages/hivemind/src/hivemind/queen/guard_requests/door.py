"""Implement GuardRequestDoor for the Queen: file a request durably; show a CRITICAL report once.

ADR-0035: the Guard Bee (the Hive's security watcher, run in the Queen's process) can only
**request** an action aimed at one Cell or one bee, and `hivemind.guard.GuardRequestDoor` is the one
seam such a request crosses. `QueenGuardDoor` is the Queen's side of it: it refuses a report that
asks for nothing, writes the request to her own table (`QueenDeps.guard.requests`), committed
before it returns, so a request filed just before a restart is still decided after it, and sets
her wake signal so her next tick decides it at once. It never decides anything itself: the
decision is hers, on her own tick, where a request outranks every Alarm and every human message.
`report_to_human` is the door's other half: a report at CRITICAL confidence reaches the human
whatever it recommends (ADR-0035), as a SECURITY Alarm naming it, pushed to every device, durable
before the call returns and shown at most once per report id (`.show`). A CRITICAL request is
filed rather than shown here, so the one Alarm the human gets for it comes from her decision and
says what she did. `GuardDoor` is the same door as a mixin `hivemind.queen.queen.Queen` inherits,
so the running Queen *is* a `GuardRequestDoor` and the composition root hands her to the Guard Bee;
`guard_door` builds the same door from `QueenDeps` and her human inbox, for a caller that holds
only those.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Called by the Guard Bee (roadmap step 10.6) through the
    `GuardRequestDoor` protocol only. Calls into `hivemind.common.logging`, `hivemind.guard`
    (GuardReport, GuardConfidence, GuardRequestDoor), `hivemind.supervision` (AlarmSeverity) and
    the sub-package's own model and show; `QueenDeps` and `HumanInbox` only for their types.

Key invariants:
    - A request's row is committed before `file_guard_request` returns; filing one report twice
      writes nothing the second time and still wakes her.
    - A CRITICAL report's Alarm is committed before `report_to_human` returns, once per report.
    - Nothing here decides, isolates, quarantines or calls a model.

See Also:
    - hivemind.guard.report for GuardReport and GuardRequestDoor, the contract implemented here.
    - hivemind.queen.guard_requests.decision for what the Queen does on the tick this wakes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.guard import GuardConfidence, GuardReport, GuardRequestDoor
from hivemind.queen.guard_requests.model import GuardRequest
from hivemind.queen.guard_requests.show import SecurityAlert, show_alert
from hivemind.supervision import AlarmSeverity
from waggle.ids import CellId, EventId

if TYPE_CHECKING:
    # Only for the type hints: hivemind.queen.deps imports this package for GuardDeps, so a real
    # import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps
    from hivemind.queen.human_inbox import HumanInbox

log = get_logger(__name__)

__all__ = ["GuardDoor", "QueenGuardDoor", "guard_door"]


class QueenGuardDoor:
    """The Queen's `GuardRequestDoor`, over her own collaborators."""

    def __init__(self, deps: QueenDeps, human_inbox: HumanInbox) -> None:
        """Hold the Queen's collaborators and her inbox of Alarms waiting on the human.

        Args:
            deps: The Queen's collaborators: her Guard request table, her wake signal.
            human_inbox: Where a CRITICAL report's Alarm waits for the human's acknowledgement.
        """
        self._deps = deps
        self._human_inbox = human_inbox

    async def file_guard_request(self, report: GuardReport) -> None:
        """Record `report` for the Queen's next tick and wake her (see `GuardRequestDoor`).

        Args:
            report: A report whose `is_request` is True.

        Raises:
            ValueError: `report` asks for nothing; the Guard Bee records such a finding alone.
        """
        if not report.is_request:
            raise ValueError(
                f"Report {report.id} ({report.recommended.value}) asks the Queen for nothing: it "
                "is the Guard Bee's to record, never a request."
            )
        request = GuardRequest(report=report, filed_at=self._deps.clock.now())
        # One local transaction (the Queen's own SQLite file in production), committed before
        # this returns: that is what "durable before it returns" means (ADR-0035).
        filed = await self._deps.guard.requests.file(request)
        log.info("queen.guard_request_filed", report_id=report.id, rule=report.rule, fresh=filed)
        # Woken either way: a repeat filing still means the Guard Bee wants a decision soon.
        self._deps.wake.set()

    async def report_to_human(self, report: GuardReport) -> None:
        """Show a CRITICAL report to the human, once (see `GuardRequestDoor`).

        Args:
            report: Any report at CRITICAL confidence, a request or not.

        Raises:
            ValueError: `report` is below CRITICAL confidence.
        """
        if report.confidence is not GuardConfidence.CRITICAL:
            raise ValueError(
                f"Report {report.id} is at {report.confidence.value} confidence: only a CRITICAL "
                "report is shown to the human; the Guard Bee records the rest alone."
            )
        if report.is_request:
            # Shown once, by her decision on it, which also says what she did (ADR-0035).
            await self.file_guard_request(report)
            return
        alert = SecurityAlert(
            severity=AlarmSeverity.CRITICAL,
            detail=f"Guard report {report.id} ({report.rule}, critical confidence) recommends "
            f"{report.recommended.value}: {report.summary}",
            cell_id=CellId(report.cell_id) if report.cell_id is not None else None,
            event_id=EventId(report.event_ids[0]),
            report_id=report.id,
        )
        # Committed before this returns: the alarm.escalated row and its chat line (durable).
        shown = await show_alert(self._deps, self._human_inbox, alert)
        log.info("queen.guard_report_shown", report_id=report.id, rule=report.rule, fresh=shown)


def guard_door(deps: QueenDeps, human_inbox: HumanInbox) -> GuardRequestDoor:
    """Return the Queen's door over `deps`, for a caller that holds only her collaborators.

    Args:
        deps: The Queen's collaborators (the same bundle her own tick runs on).
        human_inbox: Her inbox of Alarms waiting on the human.

    Returns:
        A `GuardRequestDoor` filing into `deps.guard.requests`, setting `deps.wake`, and showing
        a CRITICAL report through `human_inbox`.
    """
    return QueenGuardDoor(deps, human_inbox)


class GuardDoor:
    """The Queen's `GuardRequestDoor` as a mixin of `Queen`, so the running Queen is the door.

    Reads `self._deps` and `self._human_inbox`, set by `Queen.__init__`; never instantiated on
    its own.
    """

    _deps: QueenDeps
    _human_inbox: HumanInbox

    async def file_guard_request(self, report: GuardReport) -> None:
        """Record `report` for the Queen's next tick and wake her; see `QueenGuardDoor`.

        Args:
            report: A report whose `is_request` is True.

        Raises:
            ValueError: `report` asks for nothing.
        """
        await QueenGuardDoor(self._deps, self._human_inbox).file_guard_request(report)

    async def report_to_human(self, report: GuardReport) -> None:
        """Show a CRITICAL report to the human, once; see `QueenGuardDoor`.

        Args:
            report: Any report at CRITICAL confidence.

        Raises:
            ValueError: `report` is below CRITICAL confidence.
        """
        await QueenGuardDoor(self._deps, self._human_inbox).report_to_human(report)
