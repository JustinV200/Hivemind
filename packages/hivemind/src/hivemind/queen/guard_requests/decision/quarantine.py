"""Order a quarantine of every task a Guard request implicates, through the 10.6c path.

When the Queen's decision on a Guard request is QUARANTINE_BEE (her episode's answer, or the rule
for a report with no Cell to isolate), and when the Hive Stand's fallback replaces the isolation
she may not take there, each implicated task's Warden is sent `Intervene(QUARANTINE)` through the
one order the Queen already has (`hivemind.queen.quarantine.order_quarantine`, roadmap step
10.6c); the Warden's own quarantine path checkpoints, holds and taints the bee. The suspect episode
is the report's first cited event: the bee's memory is tainted from there on, the same point an
isolation taints a whole Cell from.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package. Called by `.act` and `.hive_stand`. Calls into
    `hivemind.brood_chamber` (Task), `hivemind.guard` (GuardReport), `hivemind.queen.isolation`
    (IsolationSite), `hivemind.queen.quarantine` (order_quarantine), `hivemind.supervision`
    (Quarantine) and waggle only.

Key invariants:
    - Never quarantines a bee itself: the order is the Queen's, the quarantine the Warden's.
    - Each order records its own `queen.decided` (the order's own invariant).

See Also:
    - hivemind.wardens.quarantine for the one quarantine code path.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import Task
from hivemind.guard import GuardReport
from hivemind.queen.isolation import IsolationSite
from hivemind.queen.quarantine import order_quarantine
from hivemind.supervision import Quarantine
from waggle.ids import TaskId

__all__ = ["quarantine_tasks"]


async def quarantine_tasks(
    site: IsolationSite, report: GuardReport, tasks: Sequence[Task]
) -> tuple[TaskId, ...]:
    """Send each task's Warden `Intervene(QUARANTINE)` for `report`; return the ones sent.

    Args:
        site: The running Queen's collaborators and attached Wardens.
        report: The Guard request's report; its first event is the suspect episode.
        tasks: The implicated tasks, each placed on a Cell.

    Returns:
        The tasks whose Warden was sent the order (a detached Warden's are left out).
    """
    ordered: list[TaskId] = []
    for task in tasks:
        lever = Quarantine(
            reason=f"Guard report {report.id} ({report.rule}) implicates task {task.id}.",
            bee=None,  # The Warden finds the bee running the task; the report names the task.
            task_id=task.id,
            suspect_episode_id=report.event_ids[0],
        )
        if await order_quarantine(site.deps, site.wardens, lever):
            ordered.append(task.id)
    return tuple(ordered)
