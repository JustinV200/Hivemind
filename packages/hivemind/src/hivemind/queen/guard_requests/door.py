"""Implement GuardRequestDoor for the Queen: file a Guard request durably, then wake her.

ADR-0035: the Guard Bee (the Hive's security watcher, run in the Queen's process) can only
**request** an action aimed at one Cell or one bee, and `hivemind.guard.GuardRequestDoor` is the one
seam such a request crosses. `QueenGuardDoor` is the Queen's side of it: it refuses a report that
asks for nothing, writes the request to her own table (`QueenDeps.guard.requests`), committed
before it returns, so a request filed just before a restart is still decided after it, and sets
her wake signal so her next tick decides it at once. It never decides anything itself: the
decision is hers, on her own tick, where a request outranks every Alarm and every human message.
`GuardDoor` is the same door as a mixin `hivemind.queen.queen.Queen` inherits, so the running
Queen *is* a `GuardRequestDoor` and the composition root hands her to the Guard Bee; `guard_door`
builds the same door from `QueenDeps` alone, for a caller inside her own tick that holds only those.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Called by the Guard Bee (roadmap step 10.6) through the
    `GuardRequestDoor` protocol only. Calls into `hivemind.common.logging`, `hivemind.guard`
    (GuardReport, GuardRequestDoor) and the sub-package's own model; `QueenDeps` only for its type.

Key invariants:
    - A request's row is committed before `file_guard_request` returns; filing one report twice
      writes nothing the second time and still wakes her.
    - Nothing here decides, isolates, quarantines or calls a model.

See Also:
    - hivemind.guard.report for GuardReport and GuardRequestDoor, the contract implemented here.
    - hivemind.queen.guard_requests.decision for what the Queen does on the tick this wakes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.guard import GuardReport, GuardRequestDoor
from hivemind.queen.guard_requests.model import GuardRequest

if TYPE_CHECKING:
    # Only for the type hints: hivemind.queen.deps imports this package for GuardDeps, so a real
    # import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps

log = get_logger(__name__)

__all__ = ["GuardDoor", "QueenGuardDoor", "guard_door"]


class QueenGuardDoor:
    """The Queen's `GuardRequestDoor`, over her own collaborators."""

    def __init__(self, deps: QueenDeps) -> None:
        """Hold the Queen's collaborators: her Guard request table and her wake signal.

        Args:
            deps: The Queen's collaborators.
        """
        self._deps = deps

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


def guard_door(deps: QueenDeps) -> GuardRequestDoor:
    """Return the Queen's door over `deps`, for a caller that holds only her collaborators.

    Args:
        deps: The Queen's collaborators (the same bundle her own tick runs on).

    Returns:
        A `GuardRequestDoor` filing into `deps.guard.requests` and setting `deps.wake`.
    """
    return QueenGuardDoor(deps)


class GuardDoor:
    """`file_guard_request` as a mixin of `Queen`, so the running Queen is the door itself.

    Reads `self._deps`, set by `Queen.__init__`; never instantiated on its own.
    """

    _deps: QueenDeps

    async def file_guard_request(self, report: GuardReport) -> None:
        """Record `report` for the Queen's next tick and wake her; see `QueenGuardDoor`.

        Args:
            report: A report whose `is_request` is True.

        Raises:
            ValueError: `report` asks for nothing.
        """
        await QueenGuardDoor(self._deps).file_guard_request(report)
