"""Define run_goal and run_requested_goal: run one goal on a running Hive and report its end.

`run_goal` submits one goal and polls until every one of its tasks reaches a terminal
`hivemind.brood_chamber.TaskStatus`, or `timeout_s` elapses, forwarding trail events of interest
to an optional `on_event` callback and syncing any answer `hive inbox answer` left in another
process (`hivemind.queen.sync_answers_from_chamber`) on every poll. `run_requested_goal` (roadmap
step 10.3c) asks the Queen for the goal as a durable goal request instead
(`hivemind.cli.compose.request`) and follows the goal she plans from it the same way.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.hive`. Called by
    `hivemind.cli.run` and every end-to-end test. Calls into `hivemind.brood_chamber`,
    `hivemind.cell`, `hivemind.cli.compose.request`, `.hive.build` (Hive), `hivemind.pheromone`,
    `hivemind.queen` (sync_answers_from_chamber) and waggle only.

Key invariants:
    - `run_goal` never blocks past `timeout_s`: `GoalReport.timed_out` is True whenever the goal's
      own tasks are not all terminal by then, and `succeeded` is False in that case regardless of
      how far the goal got.

See Also:
    - .claude/roadmap.md step 3.22 scenario (a) for the three-haiku goal `run_goal` is built to
      finish end to end.
    - hivemind.queen.sync_answers_from_chamber for the cross-process answer handoff `run_goal`
      polls for on every iteration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cli.compose.hive.build import Hive
from hivemind.cli.compose.request import GoalAsk, request_goal_and_wait
from hivemind.pheromone import LlmEvent, PheromoneEvent, TrailQuery
from hivemind.queen import sync_answers_from_chamber
from waggle.ids import TaskId

# run_goal's own polling cadence, on the injected Clock: short enough that a FakeClock-driven unit
# test (codingrules 14.5) finishes in a handful of iterations, gentle enough to be a real interval
# against a live SQLite file in production (matches fake_manifest's own short heartbeat interval).
_POLL_INTERVAL_S = 0.05

__all__ = ["GoalReport", "run_goal", "run_requested_goal"]


@dataclass(frozen=True, slots=True)
class GoalReport:
    """What `run_goal` returns once a goal's own tasks are terminal, or `timeout_s` elapsed.

    Attributes:
        goal_id: The goal's own id (`hivemind.queen.Queen.submit_goal`'s return value: the first
            minted task's id).
        tasks: Every task under this goal, in `hivemind.brood_chamber.TaskFilter` order, as of the
            moment `run_goal` stopped polling.
        succeeded: True when every task is `TaskStatus.SUCCEEDED` and `timed_out` is False.
        spend_usd: Every `llm.call` occurrence's own cost recorded on the trail from submission
            onward (Hive-wide, not goal-scoped: no per-goal Forage ledger exists this phase, see
            `_spend_since`'s own docstring).
        elapsed_s: Wall time (on the injected Clock's own `monotonic()`) from submission to when
            polling stopped.
        timed_out: True when `timeout_s` elapsed before every task reached a terminal status.
    """

    goal_id: TaskId
    tasks: tuple[Task, ...]
    succeeded: bool
    spend_usd: float
    elapsed_s: float
    timed_out: bool


async def run_goal(
    hive: Hive,
    goal: str,
    *,
    clearance: HoneyClearance,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None = None,
) -> GoalReport:
    """Submit `goal` and poll until every one of its tasks is terminal, or `timeout_s` elapses.

    Must be called inside a `run_hive` block: it submits through `hive.queen`, which only ticks
    while `run_hive`'s own background tasks are running.

    Args:
        hive: A Hive whose Queen and Warden are running (inside an `async with run_hive(hive):`).
        goal: The goal text, as the human stated it.
        clearance: The goal's own data-sensitivity ceiling.
        timeout_s: The most wall time (on `hive.clock.monotonic()`) to poll before giving up.
        on_event: Called with every new trail event of interest, oldest first, as it lands; never
            called for a re-delivered event across polls.

    Returns:
        A GoalReport: `succeeded` is True only when every task reached SUCCEEDED before the
        timeout.
    """
    clock = hive.clock
    # The deadline runs from here, before planning: submit_goal's own model call can take
    # minutes on a local model, and "never blocks past timeout_s" (module docstring) has to
    # include it.
    submission = _Submission(start=clock.monotonic(), at=clock.now())
    goal_id = await hive.queen.submit_goal(goal, clearance=clearance)
    return await _follow(hive, goal_id, timeout_s, on_event, submission)


async def run_requested_goal(
    hive: Hive,
    ask: GoalAsk,
    *,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None = None,
) -> GoalReport:
    """Ask for `ask` as a durable goal request, then follow its goal like `run_goal` does.

    Roadmap step 10.3c: a tier the operator named is asked for the way the Hive Entrance asks,
    the one way a tier like NIGHT_VEIL is initiated (`hivemind.cli.compose.request`).

    Args:
        hive: A Hive whose Queen and Warden are running (inside an `async with run_hive(hive):`).
        ask: The goal, its clearance and the tier the operator named.
        timeout_s: The most wall time to wait, planning included.
        on_event: As for `run_goal`.

    Returns:
        A GoalReport over the planned goal, as `run_goal` returns.

    Raises:
        hivemind.cli.compose.request.GoalNotPlannedError: The Queen refused the request, or it was
            not planned before the timeout.
    """
    clock = hive.clock
    submission = _Submission(start=clock.monotonic(), at=clock.now())
    deadline_s = submission.start + timeout_s
    requests = hive.stores.goal_requests
    goal_id = await request_goal_and_wait(hive.queen, requests, clock, ask, deadline_s)
    return await _follow(hive, goal_id, timeout_s, on_event, submission)


async def _follow(
    hive: Hive,
    goal_id: TaskId,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None,
    submission: _Submission,
) -> GoalReport:
    """Poll `goal_id`'s tasks to the end (or the deadline) and report on them."""
    timed_out = await _poll_until_terminal(hive, goal_id, timeout_s, on_event, submission)
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    succeeded = (
        bool(tasks) and not timed_out and all(t.status is TaskStatus.SUCCEEDED for t in tasks)
    )
    return GoalReport(
        goal_id=goal_id,
        tasks=tasks,
        succeeded=succeeded,
        spend_usd=await _spend_since(hive, submission.at),
        elapsed_s=hive.clock.monotonic() - submission.start,
        timed_out=timed_out,
    )


@dataclass(frozen=True, slots=True)
class _Submission:
    """When a goal was submitted, on both clocks `_poll_until_terminal` needs (codingrules 5.1)."""

    start: float  # `clock.monotonic()` at submission: what `timeout_s` counts from.
    at: datetime  # `clock.now()` at submission: where the forwarded trail view begins.


async def _poll_until_terminal(
    hive: Hive,
    goal_id: TaskId,
    timeout_s: float,
    on_event: Callable[[PheromoneEvent], None] | None,
    submission: _Submission,
) -> bool:
    """Poll chamber state and forward trail events until `goal_id`'s own tasks are all terminal.

    Args:
        hive: The running Hive whose chamber and trail are polled.
        goal_id: The goal whose tasks decide when polling stops.
        timeout_s: The most wall time to poll, measured from `start`.
        on_event: Called with every new trail event of interest; None to forward nothing.
        submission: When the goal was submitted. `timeout_s` counts from `submission.start`, so
            planning time (`submit_goal`'s own model call) is inside the budget; forwarded
            events begin at `submission.at`, so a store that already holds earlier runs never
            replays their whole history into this run's view.

    Returns:
        True once `timeout_s` elapsed first; False once every task reached a terminal status.
    """
    clock = hive.clock
    start = submission.start
    last_at: datetime | None = submission.at
    last_ids: set[str] = set()
    while True:
        # hive inbox answer (a separate process against the same [hive] db) can only leave a
        # human's answer for the Queen to notice on its own next poll (hive.py's own module
        # docstring); this is that poll, run from here since hivemind.queen.queen is not this
        # dispatch's file to add the call to the Queen's own tick (hivemind.queen.questions.
        # sync_answers_from_chamber's own module docstring).
        await sync_answers_from_chamber(hive.queen)
        last_at, last_ids = await _forward_new_events(hive, on_event, last_at, last_ids)
        tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        if tasks and all(is_terminal(task.status) for task in tasks):
            # The task's own terminal event (task.succeeded/failed) may have landed on the trail
            # after the query above already ran (the chamber and the trail are separate stores,
            # codingrules section 12): one more forward catches it before this stops polling, so
            # on_event's own stream always ends with the event that made the goal terminal.
            await _forward_new_events(hive, on_event, last_at, last_ids)
            return False
        if clock.monotonic() - start >= timeout_s:
            return True
        # External wait: the one real pause in this loop, always through the injected Clock so a
        # FakeClock-driven test controls every iteration instead of a real timer.
        await clock.sleep(_POLL_INTERVAL_S)


async def _forward_new_events(
    hive: Hive,
    on_event: Callable[[PheromoneEvent], None] | None,
    last_at: datetime | None,
    last_ids: set[str],
) -> tuple[datetime | None, set[str]]:
    """Query the trail since `last_at`, call `on_event` on every not-yet-seen one, advance the mark.

    Mirrors `hivemind.pheromone.trail.tail.follow`'s own "since is inclusive, track ids at the
    high-water mark too" technique, inlined here (rather than driving `follow` itself) because
    `run_goal`'s own loop already owns the polling cadence and needs to interleave a chamber
    check and `sync_answers_from_chamber` between reads, not just yield events forever.
    """
    page = await hive.stores.trail.query(TrailQuery(since=last_at))
    new_events = [
        event
        for event in page
        if not (last_at is not None and event.at == last_at and event.id in last_ids)
    ]
    if on_event is not None:
        for event in new_events:
            on_event(event)
    if not page:
        return last_at, last_ids
    newest_at = page[-1].at
    return newest_at, {event.id for event in page if event.at == newest_at}


async def _spend_since(hive: Hive, submitted_at: datetime) -> float:
    """Sum every `llm.call` occurrence's own cost recorded since `submitted_at`.

    See `GoalReport.spend_usd`'s own docstring for the Hive-wide, not goal-scoped, approximation
    this is. No per-goal Forage ledger exists this phase (`hivemind.forage.allocate.grant` is a
    pure, static allocator; codingrules section 8.10 names a live ledger a later-phase addition),
    so this is every model call the Fanner recorded on the trail since the goal was submitted --
    exact for the common case `run_goal` serves, one goal running at a time against one Hive.
    """
    events = await hive.stores.trail.query(
        TrailQuery(since=submitted_at, family="llm", kind="llm.call")
    )
    costs: list[float] = [
        event.usage.cost_usd
        for event in events
        if isinstance(event, LlmEvent)
        and event.usage is not None
        and event.usage.cost_usd is not None
    ]
    return sum(costs)
