"""Decide whether a Guard report aimed at one Cell or one bee is filed as a request to the Queen.

A request outranks every Alarm and every human message in the Queen's inbox (ADR-0035), so a noisy
rule could crowd her attention; three limits, all data, keep it from doing so. A report below
`[guard] request_confidence` is never filed: it stays a `guard.alert`. A repeat of one rule
against one target (its Cell, else its first bee or task) inside `[guard.bee] coalesce_window_s`
of the last request filed for them is coalesced into that one. And no more than `[guard]
requests_per_hour` are filed in any hour; one over the cap is still a `guard.alert`, recorded as
capped. `RequestLedger` holds what it takes to apply those limits: when each rule and target last
filed, and the moments of the last hour's requests; after a restart it is rebuilt from the Guard
Bee's own `guard.alert` events, which record every request filed with its rule and target.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Owned by
    `.respond.FindingResponder`, which asks `admit` before filing through the Queen's door and
    tells `record` after. Calls into `hivemind.guard` (GuardConfidence, GuardReport) only.

Key invariants:
    - Only a filed request counts toward coalescing and the cap; a coalesced, capped or
      below-floor report never does, so a burst of weak reports cannot starve a strong one.
    - The floor is checked first: a report below it is BELOW_FLOOR whatever the other two say.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.guard.report for GuardReport and GuardRequestDoor.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from enum import Enum

from hivemind.guard import GuardConfidence, GuardReport

CAP_WINDOW = timedelta(hours=1)  # The window `[guard] requests_per_hour` counts over.
HIVE_TARGET = "hive"  # The target of a report that names no Cell, bee or task.

__all__ = ["CAP_WINDOW", "HIVE_TARGET", "Disposition", "RequestLedger", "request_target"]


class Disposition(Enum):
    """What became of one report; recorded on its `guard.alert`."""

    FILED = "filed"  # A request, filed through the Queen's door.
    COALESCED = "coalesced"  # A request folded into the one filed for its rule and target.
    CAPPED = "capped"  # A request over the hourly cap.
    BELOW_FLOOR = "below_floor"  # A request under [guard] request_confidence.
    RAISED = "raised"  # A Capping tier's sampled-audit rate was raised.
    AT_CEILING = "at_ceiling"  # The tier samples everything already: nothing left to raise.
    REDUCE_ORDERED = "reduce_ordered"  # The Entrance Reducer was ordered.
    OBSERVED = "observed"  # Nothing to do: the alert is the record.


def request_target(report: GuardReport) -> str:
    """Return what a request is aimed at for coalescing: its Cell, else its first bee or task.

    Args:
        report: A report recommending one of the request actions.

    Returns:
        A Cell, bee or task id, or `HIVE_TARGET` for a report that names none.
    """
    for candidate in (report.cell_id, *report.bee_ids, *report.task_ids):
        if candidate is not None:
            return candidate
    return HIVE_TARGET


class RequestLedger:
    """The requests the Guard Bee filed recently: per rule and target, and over the last hour.

    Owns its own mutable state (codingrules 8.5): `record` and `restore` are the only writes.
    """

    def __init__(self, floor: GuardConfidence, per_hour: int, coalesce_window_s: float) -> None:
        """Build a ledger that has filed nothing yet.

        Args:
            floor: `[guard] request_confidence`: the least a filed request is sure of.
            per_hour: `[guard] requests_per_hour`; >= 1.
            coalesce_window_s: `[guard.bee] coalesce_window_s`; 0 files every repeat.
        """
        self._floor = floor
        self._per_hour = per_hour
        self._coalesce = timedelta(seconds=coalesce_window_s)
        self._last_filed: dict[tuple[str, str], datetime] = {}
        self._filed_at: deque[datetime] = deque()

    def admit(self, report: GuardReport, now: datetime) -> Disposition:
        """Decide what becomes of a report that recommends a request, without recording it.

        Args:
            report: A report whose `is_request` is True.
            now: The moment it would be filed.

        Returns:
            FILED when every limit allows it, else BELOW_FLOOR, COALESCED or CAPPED.
        """
        if not report.confidence.at_least(self._floor):
            return Disposition.BELOW_FLOOR
        last = self._last_filed.get((report.rule, request_target(report)))
        if last is not None and now - last < self._coalesce:
            return Disposition.COALESCED
        self._forget_before(now - CAP_WINDOW)
        if len(self._filed_at) >= self._per_hour:
            return Disposition.CAPPED
        return Disposition.FILED

    def record(self, report: GuardReport, at: datetime) -> None:
        """Remember a request filed through the Queen's door at `at`.

        Args:
            report: The filed report.
            at: When it was filed.
        """
        self.restore(report.rule, request_target(report), at)

    def restore(self, rule: str, target: str, at: datetime) -> None:
        """Remember a request filed before a restart, as its own `guard.alert` recorded it.

        Args:
            rule: The rule key the alert names.
            target: The request target the alert names.
            at: When the alert was recorded.
        """
        key = (rule, target)
        self._last_filed[key] = max(self._last_filed.get(key, at), at)
        self._filed_at.append(at)
        # Kept in time order: a restored alert may be older than one already remembered.
        self._filed_at = deque(sorted(self._filed_at))

    def _forget_before(self, cutoff: datetime) -> None:
        """Drop the filed moments that fell out of the cap's hour."""
        while self._filed_at and self._filed_at[0] <= cutoff:
            self._filed_at.popleft()
