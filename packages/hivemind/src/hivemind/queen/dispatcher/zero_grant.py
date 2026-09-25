"""Decide what a fresh grant that runs no bee does: wait for room, or be denied at once.

A grant can run no bee for two kinds of reason, and they call for opposite answers. A lasting one
-- the Guard's grant_issue point removed every binding, the Cell's own cap is zero, its whole
memory or all of its cores could never hold one footprint, no allowed source offers a seat once
the Royal Reserve's are held, or a Cell whose capacity is fixed for its life (a Virtual Cell's
spec) is short -- fails the task at once with `forage.denied` and the figures (`deny_zero_grant`),
exactly as the phase 4 fix did (`.claude/phase-4-handoff.md` section 4.2 item 1: a grant that
empty used to be sent anyway and park its task RUNNING until its timeout, nothing on screen). A
passing one -- the free cores or free memory of a Cell whose capacity is read live (the Hive
Stand's one-minute load), every seat its allowed sources offer busy right now (calls in flight,
or a provider's rate limit masking its seats until the window passes), or a goal whose other
running tasks hold its whole sub-bee allowance -- used to fail the task too, so a busy moment on
the host or on a provider failed every goal outright. Such a task now waits: it stays PENDING, the
first pass records one `forage.denied` with `deferred = true`, the limit waited on and the
figures, later passes record nothing, and each pass tries again (`settle_wait`, `hold_for_room`).
A wait on a passing figure (the Cell's live ones, or the seats) is bounded by `[forage]
zero_grant_patience_s` and then fails the task like a lasting shortfall, the wait's length in its
summary, so nothing waits for ever unseen; a wait on the goal's own allowance ends when one of its
tasks does. Busy seats are read from the Forage map, never from a Cell, so they are waited out
before any Cell is chosen (`hold_for_room`), exactly like the goal's allowance: a Cell acquired
for a task that then waits for a seat would sit idle. A fresh Virtual Cell is sized the same way
before it is provisioned, from the capacity its spec promises (`ready_to_provision`): a grant that
could run no bee there, for good or once its wait's patience is spent, refuses the task then and
there instead of provisioning a Cell only to deny its grant and release it. Such a task never left
PENDING, so it is cancelled (PENDING's one edge to an end) with the denial as its reason, after the
same `forage.denied` a denial on a Cell records. Which limit was tightest comes from the allocator
itself as data (`hivemind.forage.SubBeeLimits`), never from parsing a reason string.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready` on every dispatch pass. Calls into
    `hivemind.brood_chamber` (Task, TaskFilter, TaskOutcome, TaskStatus), `hivemind.forage`
    (GrantBound, RoyalReserve, SubBeeLimits, goal_limit, seat_limit), `hivemind.queen.deps`
    (GrantWait, QueenDeps), `hivemind.queen.dispatcher.sizing` (SizedGrant, size_for_capacity),
    `hivemind.queen.intake` (goal_budgets), `hivemind.queen.placement` (Placement,
    ProvisionVirtual), `hivemind.queen.trail` (record_forage_event) and waggle only.

Key invariants:
    - A wait records exactly one `forage.denied` (`deferred = true`) when it starts, or when it
      moves between a passing figure and the goal's own allowance, and nothing on any other
      pass; nothing is ever sent to a Warden for a waiting task, and the chamber never moves it
      out of PENDING.
    - Only a limit whose figure can change while the task waits is waited out: `FREE_CORES` and
      `FREE_MEMORY` only on a Cell whose reading is live, `SEATS` and `GOAL_BEES` on any Cell,
      `CELL_CAP` never, and no limit at all that leaves no bee even at its best.
    - A wait on a passing figure ends in a grant or, once `patience_s` has passed, in
      `deny_zero_grant`; the task never waits on one for longer.
    - `deny_zero_grant` always records `forage.denied` before it fails the task, so the cause is
      on the trail before the outcome is; `ready_to_provision` does the same before it cancels.
    - No fresh Virtual Cell is provisioned for a task whose grant there could run no bee.

See Also:
    - .claude/phase-4-handoff.md section 4.2 item 1 for the silent park the denial still prevents.
    - hivemind.forage.allocate for SubBeeLimits, the limits every decision here reads.
    - hivemind.queen.deps for GrantWaits, the book of waits this module keeps.
    - hivemind.queen.dispatcher.ready for the dispatch pass that calls every function here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection

from pydantic import JsonValue

from hivemind.brood_chamber import Task, TaskFilter, TaskOutcome, TaskStatus
from hivemind.forage import GrantBound, RoyalReserve, SubBeeLimits, goal_limit, seat_limit
from hivemind.queen.deps import GrantWait, QueenDeps
from hivemind.queen.dispatcher.sizing import SizedGrant, size_for_capacity
from hivemind.queen.intake import goal_budgets
from hivemind.queen.placement import Placement, ProvisionVirtual
from hivemind.queen.trail import record_forage_event
from waggle.ids import TaskId

# A goal's own allowance is freed as its own tasks finish: the goal's own progress, not a host
# figure that may never move, so a task may always wait on it, with no patience of its own (one
# would fail a healthy goal whose tasks simply run longer than the patience).
_UNTIMED = frozenset({GrantBound.GOAL_BEES})
# Busy seats free up as calls end and rate limits lapse, whichever Cell the task would run on:
# the Forage map reads them live for every Cell alike, so they pass anywhere, within patience.
_PASSING_ANYWHERE = _UNTIMED | {GrantBound.SEATS}
# The free figures of a Cell read live change while a task waits; on a Cell whose capacity is
# fixed for its life they never will, so waiting on them there would only delay the same denial.
_PASSING_WHEN_LIVE = frozenset({GrantBound.FREE_CORES, GrantBound.FREE_MEMORY})
# A sibling that has been assigned and has not finished runs one sub-bee: a Warden spawns one per
# assignment (hivemind.wardens.ticks.assign.handle_assign), and a paused one resumes into its own.
_HOLDS_A_BEE = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED}
)

__all__ = [
    "deny_zero_grant",
    "forget_waits",
    "hold_for_room",
    "ready_to_provision",
    "settle_wait",
    "waits_on",
]


def waits_on(limits: SubBeeLimits, *, is_live: bool) -> GrantBound | None:
    """Return the limit a grant with no bee may wait on, or None when it must be denied at once.

    Args:
        limits: The grant's own limits (`hivemind.forage.sub_bee_limits`), or a goal's alone
            (`hivemind.forage.goal_limit`).
        is_live: Whether the Cell's capacity reading is refreshed live.

    Returns:
        The tightest limit when every limit that leaves no bee now is one a wait can lift;
        None when the limits leave a bee, or any limit that leaves none never lifts.

    Example:
        >>> waits_on(limits_at_high_load, is_live=True)  # doctest: +SKIP
        <GrantBound.FREE_CORES: 'free_cores'>
    """
    short = limits.short()
    # Room for a bee (nothing to wait for), or a limit no wait lifts (nothing worth waiting for).
    if not short or limits.never_lifts():
        return None
    return limits.limited_by if short <= _passing(is_live=is_live) else None


async def hold_for_room(deps: QueenDeps, task: Task) -> bool:
    """Hold `task` PENDING while its goal's allowance, or every seat it may use, is taken.

    Checked before any Cell is chosen, since neither depends on one: a Cell acquired for a task
    that then waits would sit idle, and a fresh Virtual one would be provisioned again on the next
    pass. The allowance counts the bees the goal's siblings run (one per started, unfinished task),
    not their grants' ceilings: a ceiling is only the most a Warden may spawn, and the Hive Stand's
    Warden sizes its one pool from the latest grant it holds (`hivemind.wardens.ticks.assign.
    handle_grant`). The seats are those the task's tempo allows on the Forage map, less the Royal
    Reserve's, exactly as its grant would count them (`hivemind.forage.seat_limit`).

    Args:
        deps: The Queen's collaborators; `dispatch.waits` is read and written.
        task: The ready task about to be placed.

    Returns:
        True when the task waits (its wait's one event recorded when it began); False when there
        is room, when a shortfall never lifts (a cap too small for even one bee, a reserve holding
        every seat: denied once the task is placed), or when a wait for seats has outlasted
        `[forage] zero_grant_patience_s` (placed, so its grant is denied with the figures).
    """
    if await _hold_for_goal(deps, task):
        return True
    return await _hold_for_seats(deps, task)


async def _hold_for_goal(deps: QueenDeps, task: Task) -> bool:
    """Hold `task` while its goal's other running tasks hold its whole allowance (untimed)."""
    used = await _goal_bees_in_use(deps, task)
    budgets = goal_budgets(deps.budgets, task.spec)
    limits = goal_limit(budgets, used, deps.reserve, task.spec.needs.tempo)
    bound = waits_on(limits, is_live=False)
    if bound is None:
        return False
    await _note_wait(deps, task, bound, _goal_figures(task, limits, used))
    return True


async def _hold_for_seats(deps: QueenDeps, task: Task) -> bool:
    """Hold `task` while every seat its tempo may use is busy, within the wait's patience."""
    budgets = goal_budgets(deps.budgets, task.spec)
    limits = seat_limit(deps.map, budgets, deps.reserve, task.spec.needs.tempo)
    # Seats pass on any Cell (_PASSING_ANYWHERE): no Cell's reading is involved here at all.
    bound = waits_on(limits, is_live=False)
    if bound is None:
        return False
    wait = await _note_wait(deps, task, bound, _seat_figures(task, limits, deps.reserve))
    # Past its patience the task is placed after all: its grant, sized on the same seats, is then
    # denied with the figures by settle_wait, the same end a wait on the host's figures meets.
    return (deps.clock.now() - wait.since).total_seconds() < deps.dispatch.waits.patience_s


async def ready_to_provision(deps: QueenDeps, task: Task, placement: Placement) -> bool:
    """Return whether the Virtual Cell `placement` names may be made for `task` (module docstring).

    Args:
        deps: The Queen's collaborators.
        task: The PENDING task the Cell would be made for.
        placement: A Virtual Placement; a fresh provision is sized from its spec's capacity. A
            dormant Cell's figures are its lifecycle's, not placement's, so it is resumed and
            sized once its link exists, as an attached Cell is.

    Returns:
        True when the Cell may be made; False when the task waits (busy seats, within their
        patience) or has been refused for good (`forage.denied` recorded, the task cancelled).
    """
    if not isinstance(placement, ProvisionVirtual):
        return True
    settled = await settle_wait(deps, task, size_for_capacity(deps, placement.spec.capacity, task))
    if settled is None:
        return False  # A passing shortfall: still PENDING, and nothing is made meanwhile.
    if settled.grant.max_sub_bees >= 1:
        return True
    bound = _blame(settled)
    # The task is the subject: no grant was issued, and its placeholder ids name nothing real.
    figures = {**_figures(settled), "warden_id": None, "cell_id": None}
    limited_by = bound.value if bound is not None else None
    await record_forage_event(
        deps,
        "forage.denied",
        task.id,
        deferred=False,
        limited_by=limited_by,
        waited_s=settled.waited_s,
        **figures,
    )
    await deps.chamber.cancel(task.id, _denial_summary(settled, bound))
    return False


async def settle_wait(deps: QueenDeps, task: Task, sized: SizedGrant) -> SizedGrant | None:
    """Hold a fresh task whose grant a passing shortfall zeroed, or hand its grant back to send.

    Args:
        deps: The Queen's collaborators; `dispatch.waits` is read and written.
        task: The PENDING task the grant was sized for.
        sized: Its freshly sized grant (`hivemind.queen.dispatcher.sizing.size_grant`).

    Returns:
        None while the task waits; otherwise `sized` -- unchanged when it runs a bee or its zero
        never lifts, or with `waited_s` set when a wait on the Cell's live figures outlasted
        `[forage] zero_grant_patience_s` -- for the caller to assign, then send or deny.
    """
    runs_a_bee = sized.grant.max_sub_bees >= 1
    bound = None if runs_a_bee else waits_on(sized.limits, is_live=sized.is_live)
    if bound is None:
        # A grant to send, or a zero no wait lifts: either way this task waits no longer.
        deps.dispatch.waits.waits.pop(task.id, None)
        return sized
    wait = await _note_wait(deps, task, bound, _figures(sized))
    waited_s = (deps.clock.now() - wait.since).total_seconds()
    if not _is_timed(bound) or waited_s < deps.dispatch.waits.patience_s:
        return None
    # Patience spent: the task fails with the figures (deny_zero_grant) rather than wait on.
    deps.dispatch.waits.waits.pop(task.id, None)
    return dataclasses.replace(sized, waited_s=waited_s)


async def deny_zero_grant(deps: QueenDeps, task: Task, sized: SizedGrant) -> None:
    """Record `forage.denied` for a grant that can run no bee, then fail `task` with the cause.

    Two ways a grant runs no bee: it computed to zero sub-bees, or (roadmap step 10.3) the
    grant_issue point removed every model binding it named; `limited_by` on the event (None for
    the latter) and the outcome's own summary say which. The event carries the allocator's own
    reason plus the core, load, memory, reserve and seat figures that produced zero, so an
    operator reading the trail -- or `hive run`'s own streamed line -- never has to guess why.

    Args:
        deps: The Queen's collaborators.
        task: The RUNNING task the grant was for (a fresh dispatch assigns and starts it first).
        sized: The grant that runs no bee, with `waited_s` set when a wait ran out of patience.
    """
    bound = _blame(sized)
    await record_forage_event(
        deps,
        "forage.denied",
        sized.grant.id,
        deferred=False,
        limited_by=bound.value if bound is not None else None,
        waited_s=sized.waited_s,
        **_figures(sized),
    )
    outcome = TaskOutcome(status=TaskStatus.FAILED, summary=_denial_summary(sized, bound))
    await deps.chamber.fail(task.id, outcome)


def forget_waits(deps: QueenDeps, ready: Collection[TaskId]) -> None:
    """Drop every wait, for a grant or for a Cell, of a task no longer ready to dispatch.

    A wait ends when its task leaves PENDING (placed, cancelled, or failed once its patience ran
    out), so a later wait of the same task is said afresh, once.

    Args:
        deps: The Queen's collaborators; `dispatch.waits.waits` and `dispatch.unplaced` are
            pruned in place.
        ready: The ids of every task ready to dispatch on this pass.
    """
    for book in (deps.dispatch.waits.waits, deps.dispatch.unplaced):
        for task_id in [task_id for task_id in book if task_id not in ready]:
            del book[task_id]


def _passing(*, is_live: bool) -> frozenset[GrantBound]:
    """Return the limits a wait can lift, given whether the Cell's reading is live."""
    return _PASSING_ANYWHERE | (_PASSING_WHEN_LIVE if is_live else frozenset())


def _is_timed(bound: GrantBound) -> bool:
    """Return whether a wait on `bound` is bounded by `[forage] zero_grant_patience_s`."""
    return bound not in _UNTIMED


async def _note_wait(
    deps: QueenDeps, task: Task, bound: GrantBound, figures: dict[str, JsonValue]
) -> GrantWait:
    """Start or continue `task`'s wait on `bound`, recording the one event a new wait gets."""
    book = deps.dispatch.waits
    previous = book.waits.get(task.id)
    # Still waiting on the same kind of shortfall: already said once, so say nothing more and keep
    # the clock. Which passing figure is tightest may change from pass to pass (cores one pass,
    # memory the next, busy seats after that) without the task ever having had room, so that is
    # one wait, and its patience runs from when it began.
    if previous is not None and _is_timed(previous.bound) == _is_timed(bound):
        return previous
    # A new wait, or one moving between a passing figure and the goal's own allowance: a
    # different cause with a different end, so it is said once and runs its own clock.
    wait = GrantWait(bound=bound, since=deps.clock.now())
    book.waits[task.id] = wait
    patience_s = book.patience_s if _is_timed(bound) else None
    await record_forage_event(
        deps,
        "forage.denied",
        task.id,
        deferred=True,
        limited_by=bound.value,
        patience_s=patience_s,
        **figures,
    )
    return wait


async def _goal_bees_in_use(deps: QueenDeps, task: Task) -> int:
    """Count the sub-bees `task`'s goal's other started, unfinished tasks run: one each."""
    siblings = await deps.chamber.list(TaskFilter(goal_id=task.goal_id))
    return sum(1 for other in siblings if other.id != task.id and other.status in _HOLDS_A_BEE)


def _blame(sized: SizedGrant) -> GrantBound | None:
    """Return the limit a denial names: one that never lifts, else one no wait could lift."""
    if sized.grant.max_sub_bees >= 1:
        return None  # It sized a bee; the grant_issue point removed every binding it named.
    limits = sized.limits
    if limits.lasting is not None:
        return limits.lasting
    stuck = limits.short() - _passing(is_live=sized.is_live)
    # Nothing stuck means every short limit could pass: a wait ran out of patience on the
    # tightest of them.
    return next((bound for bound in GrantBound if bound in stuck), limits.limited_by)


def _denial_summary(sized: SizedGrant, bound: GrantBound | None) -> str:
    """Build the failed task's own summary: what the grant allows, any wait, and the reason."""
    what = "zero sub-bees" if sized.grant.max_sub_bees < 1 else "no model binding"
    if sized.waited_s is None or bound is None:
        return f"Forage denied: grant allows {what}. {sized.grant.reason}"
    return (
        f"Forage denied: grant allows {what} after waiting {sized.waited_s:.0f}s for "
        f"{bound.value}. {sized.grant.reason}"
    )


def _figures(sized: SizedGrant) -> dict[str, JsonValue]:
    """Return the figures a Cell's grant was sized from, for a `forage.denied` payload."""
    inputs = sized.inputs
    host = inputs.cell_capacity.host
    return {
        "task_id": inputs.task_id,
        "warden_id": inputs.holder,
        "cell_id": inputs.cell_id,
        "max_sub_bees": sized.grant.max_sub_bees,
        "cell_cap": inputs.cell_capacity.max_sub_bees,
        "cores": host.cores,
        "cpu_load": host.cpu_load,
        "free_memory_bytes": host.memory_free_bytes,
        "reserve_memory_bytes": inputs.reserve.memory_bytes,
        "reserve_seats": inputs.reserve.seats,
        "footprint_cpu_cores": inputs.footprint.cpu_cores,
        "footprint_memory_bytes": inputs.footprint.memory_bytes,
        "allowed_bindings": len(sized.grant.allowed),
        "reason": sized.grant.reason,
    }


def _goal_figures(task: Task, limits: SubBeeLimits, used: int) -> dict[str, JsonValue]:
    """Return the figures a goal's allowance was read from, for a `forage.denied` payload."""
    cap = limits.at_best[GrantBound.GOAL_BEES]
    return {
        "task_id": task.id,
        "goal_id": task.goal_id,
        "max_sub_bees": limits.max_sub_bees,
        "goal_cap": cap,
        "goal_sub_bees_used": used,
        "reason": (
            f"goal {task.goal_id}: {used} of its {cap} sub-bees are held by its running tasks, "
            f"leaving {limits.max_sub_bees} once the {1 - limits.margin:.0%} headroom is taken."
        ),
    }


def _seat_figures(task: Task, limits: SubBeeLimits, reserve: RoyalReserve) -> dict[str, JsonValue]:
    """Return the figures the seats were read from, for a `forage.denied` payload."""
    free, every = limits.now[GrantBound.SEATS], limits.at_best[GrantBound.SEATS]
    return {
        "task_id": task.id,
        "goal_id": task.goal_id,
        "max_sub_bees": limits.max_sub_bees,
        "seats_free": free,
        "seats_at_best": every,
        "reserve_seats": reserve.seats,
        "reason": (
            f"every seat its tempo's sources offer is busy: {free} free of {every} once the Royal "
            f"Reserve's {reserve.seats} are held, leaving {limits.max_sub_bees} after the "
            f"{1 - limits.margin:.0%} headroom."
        ),
    }
