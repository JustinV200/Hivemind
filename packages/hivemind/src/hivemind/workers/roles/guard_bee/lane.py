"""Run the Guard Bee's awake episodes one at a time beside the Queen's tick, never inside it.

A judge episode is one model call (seconds to a minute); run inside the Queen's tick it would hold
every Warden's Heartbeat unread long enough to mark a healthy Warden offline, the same reason the
Queen plans a goal in a lane of her own (`hivemind.queen.deps.PlanningLane`). `JudgeLane` keeps at
most one episode in flight as an owned asyncio task, and a bounded queue of findings waiting for
one: the Guard Bee's round offers a finding and returns at once, a later round reaps the verdict
and reports it, and `aclose` cancels an episode still in flight (its finding is not lost: nothing
was reported, so the rebuilt windows find it again after a restart). A full queue refuses the
offer, and the finding is reported on its rule's own verdict at once, since a verdict late is
worse than a verdict unjudged.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Owned by
    `.bee.GuardBee`; runs `.judge.GuardJudge.judge`. Calls into asyncio and this package's `.judge`
    only.

Key invariants:
    - At most one episode is in flight; every task it starts is reaped or cancelled by it
      (codingrules 11: every task has an owner that awaits or cancels it).
    - A finding is either queued, in flight or reaped: never two of those, never dropped silently.
    - An episode that raised is reaped as None (the rule's verdict stands), and logged.

See Also:
    - hivemind.workers.roles.guard_bee.judge for the episode itself.
    - hivemind.queen.deps.PlanningLane for the same shape applied to goal planning.
"""

from __future__ import annotations

import asyncio
from collections import deque

from hivemind.common.logging import get_logger
from hivemind.workers.roles.guard_bee.judge import GuardJudge, JudgeCase, Verdict

DEFAULT_LANE_CAPACITY = 16  # Findings waiting for judgement; past this they are not judged.

log = get_logger(__name__)

__all__ = ["DEFAULT_LANE_CAPACITY", "JudgeLane", "Judged"]

Judged = tuple[JudgeCase, Verdict | None]  # One finished episode: its case and its verdict.


class JudgeLane:
    """One awake episode in flight at a time, the findings queued for one, and the finished ones.

    Owns its own mutable state (codingrules 8.5): the queue, the task in flight and its case.
    """

    def __init__(self, judge: GuardJudge, capacity: int = DEFAULT_LANE_CAPACITY) -> None:
        """Build an idle lane over `judge`.

        Args:
            judge: Runs one episode per finding.
            capacity: The most findings that may wait for an episode at once; > 0.
        """
        self._judge = judge
        self._capacity = capacity
        self._queue: deque[JudgeCase] = deque()
        self._current: JudgeCase | None = None
        self._task: asyncio.Task[Verdict | None] | None = None

    def pending(self) -> frozenset[tuple[str, str]]:
        """Return the (rule, key) of every finding queued or in flight."""
        cases = [*self._queue, *([self._current] if self._current is not None else [])]
        return frozenset((case.finding.rule.key, case.finding.key) for case in cases)

    def offer(self, case: JudgeCase) -> bool:
        """Queue `case` for an episode; False when the queue is full (report it unjudged).

        Args:
            case: A finding whose rule asks for judgement.

        Returns:
            True once queued.
        """
        if len(self._queue) >= self._capacity:
            log.warning("guard_bee.judge_lane_full", rule=case.finding.rule.key)
            return False
        self._queue.append(case)
        return True

    def start(self) -> None:
        """Start the next queued episode, when none is in flight."""
        if self._task is not None or not self._queue:
            return
        self._current = self._queue.popleft()
        # Owned: the handle is kept and the task is reaped by `reap` or cancelled by `aclose`.
        self._task = asyncio.ensure_future(self._judge.judge(self._current))

    def reap(self) -> list[Judged]:
        """Return the finished episode, if the one in flight is done; start nothing.

        Returns:
            One `(case, verdict)` when the episode in flight has finished, else nothing.
        """
        task, case = self._task, self._current
        if task is None or case is None or not task.done():
            return []
        self._task, self._current = None, None
        return [(case, _verdict_of(task, case))]

    async def drain(self) -> list[Judged]:
        """Run every queued episode to its end, one at a time, and return every result.

        Each episode is bounded by the judge's own timeout, so draining a bounded queue ends.

        Returns:
            Every `(case, verdict)`, in the order the episodes ran.
        """
        finished: list[Judged] = []
        while self._task is not None or self._queue:
            self.start()
            if self._task is not None:
                # External wait: one bounded model call (the judge's own timeout caps it).
                await asyncio.wait({self._task})
            finished.extend(self.reap())
        return finished

    async def aclose(self) -> None:
        """Cancel the episode in flight and forget the queue; its finding is reported later.

        Nothing is lost by cancelling: an unreported finding is found again from the trail.
        """
        self._queue.clear()
        task, self._task, self._current = self._task, None, None
        if task is not None:
            task.cancel()
            # Waits for the cancellation to land; asyncio.wait never raises the task's error.
            await asyncio.wait({task})


def _verdict_of(task: asyncio.Task[Verdict | None], case: JudgeCase) -> Verdict | None:
    """Return a finished task's verdict, or None (logged) when it was cancelled or raised."""
    if task.cancelled():
        return None
    error = task.exception()
    if error is not None:
        log.error("guard_bee.judge_failed", rule=case.finding.rule.key, error=type(error).__name__)
        return None
    return task.result()
