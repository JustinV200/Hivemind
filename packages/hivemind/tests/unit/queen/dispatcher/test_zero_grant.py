"""Tests for hivemind.queen.dispatcher.zero_grant: which zero grants wait, and the book of waits.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/zero_grant.py (codingrules section 3). The dispatch
    flows that drive these functions end to end (defer, retry, expire, deny) are in
    test_ready_waits.py beside this module.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.zero_grant for the module under test.
    - hivemind.forage.allocate for SubBeeLimits, the limits every case here is built from.
"""

from __future__ import annotations

import dataclasses

from builders.forage import make_capacity, make_host_capacity, make_reserve
from builders.queen import FORAGE_SOURCE_SEATS, make_queen_deps, plan_responder
from builders.tasks import make_task

from hivemind.cell import HoneyClearance
from hivemind.forage import GrantBound, SubBeeLimits
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher.sizing import size_for_capacity
from hivemind.queen.dispatcher.zero_grant import deny_zero_grant, forget_waits, waits_on
from hivemind.queen.holds import GrantWait
from hivemind.queen.queen import Queen

_MARGIN = 0.9  # The default 10% headroom margin.
_ROOMY = 4  # A limit value that leaves whole bees under the margin.
_MIB = 1024**2
_GIB = 1024**3


def _limits(now: dict[GrantBound, int], at_best: dict[GrantBound, int]) -> SubBeeLimits:
    """Build limits where every bound not named is roomy both now and at best."""
    full_now = {bound: now.get(bound, _ROOMY) for bound in GrantBound}
    full_best = {bound: at_best.get(bound, max(_ROOMY, full_now[bound])) for bound in GrantBound}
    return SubBeeLimits(now=full_now, at_best=full_best, margin=_MARGIN)


def test_a_live_cells_free_cores_short_right_now_are_waited_out() -> None:
    limits = _limits({GrantBound.FREE_CORES: 0}, {GrantBound.FREE_CORES: 8})

    assert waits_on(limits, is_live=True) is GrantBound.FREE_CORES


def test_a_live_cells_free_memory_short_right_now_is_waited_out() -> None:
    limits = _limits({GrantBound.FREE_MEMORY: 0}, {GrantBound.FREE_MEMORY: 15})

    assert waits_on(limits, is_live=True) is GrantBound.FREE_MEMORY


def test_the_same_shortfall_on_a_cell_with_a_fixed_capacity_is_denied_at_once() -> None:
    # A Virtual Cell's reading is its spec: nothing will ever re-read it, so waiting is futile.
    limits = _limits({GrantBound.FREE_CORES: 0}, {GrantBound.FREE_CORES: 8})

    assert waits_on(limits, is_live=False) is None


def test_a_limit_that_never_lifts_is_denied_at_once_even_beside_a_passing_one() -> None:
    limits = _limits(
        {GrantBound.FREE_CORES: 0, GrantBound.SEATS: 0},
        {GrantBound.FREE_CORES: 8, GrantBound.SEATS: 0},
    )

    assert waits_on(limits, is_live=True) is None


def test_busy_seats_are_waited_out_on_any_cell() -> None:
    # Every allowed seat taken right now, but not for good: calls end and rate limits lapse, and
    # the Forage map reads seats live whatever Cell the task would run on.
    limits = _limits({GrantBound.SEATS: 0}, {GrantBound.SEATS: 3})

    assert waits_on(limits, is_live=True) is GrantBound.SEATS
    assert waits_on(limits, is_live=False) is GrantBound.SEATS


def test_a_passing_shortfall_beside_a_denied_one_is_denied_at_once() -> None:
    # Busy seats could pass, but a fixed Cell's free cores never will: nothing worth waiting for.
    limits = _limits(
        {GrantBound.FREE_CORES: 0, GrantBound.SEATS: 0},
        {GrantBound.FREE_CORES: 8, GrantBound.SEATS: 3},
    )

    assert waits_on(limits, is_live=False) is None


def test_the_goals_own_allowance_is_waited_out_on_any_cell() -> None:
    limits = SubBeeLimits(
        now={GrantBound.GOAL_BEES: 1}, at_best={GrantBound.GOAL_BEES: 2}, margin=_MARGIN
    )

    assert waits_on(limits, is_live=False) is GrantBound.GOAL_BEES


def test_limits_that_leave_a_bee_wait_on_nothing() -> None:
    assert waits_on(_limits({}, {}), is_live=True) is None


async def test_forget_waits_drops_every_task_no_longer_ready_and_keeps_the_rest() -> None:
    deps, _link, _end = make_queen_deps()
    ready, gone = make_task(), make_task()
    for task in (ready, gone):
        wait = GrantWait(bound=GrantBound.FREE_CORES, since=deps.clock.now())
        deps.dispatch.waits.waits[task.id] = wait

    forget_waits(deps, {ready.id})

    assert set(deps.dispatch.waits.waits) == {ready.id}


def _one_task_plan(goal: str) -> dict[str, object]:
    """A one-task plan, so a submitted goal leaves exactly one RUNNING task to deny."""
    check: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": "scratch/done.txt",
        "argv": [],
        "expected": None,
    }
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [check],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


async def test_a_denial_reports_the_reserve_memory_forage_actually_held_on_that_cell() -> None:
    # Arrange: a running task, and a 1 GiB Cell on which the 512 MiB reserve scales down to an
    # eighth (RoyalReserve.memory_for); every seat is reserved, so the grant sizes to zero.
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    task_id = await queen.submit_goal("Do a thing.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    await warden_end.close()
    reserve = make_reserve(seats=FORAGE_SOURCE_SEATS, memory_bytes=512 * _MIB)
    zeroing = dataclasses.replace(deps, reserve=reserve)
    host = make_host_capacity(memory_bytes=_GIB, memory_free_bytes=_GIB // 2)
    task = await deps.chamber.get(task_id)
    sized = size_for_capacity(zeroing, make_capacity(host=host), task)

    await deny_zero_grant(zeroing, task, sized)

    [denied] = await deps.trail.query(TrailQuery(kind="forage.denied"))
    # The figure Forage's own decision used (128 MiB here), not the configured 512 MiB ceiling.
    assert denied.payload["reserve_memory_bytes"] == reserve.memory_for(_GIB)
    assert denied.payload["reserve_memory_bytes"] == _GIB // 8
