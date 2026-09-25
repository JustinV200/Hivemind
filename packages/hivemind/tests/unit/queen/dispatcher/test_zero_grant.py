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

from builders.queen import make_queen_deps
from builders.tasks import make_task

from hivemind.forage import GrantBound, SubBeeLimits
from hivemind.queen.deps import GrantWait
from hivemind.queen.dispatcher.zero_grant import forget_waits, waits_on

_MARGIN = 0.9  # The default 10% headroom margin.
_ROOMY = 4  # A limit value that leaves whole bees under the margin.


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


def test_seats_short_right_now_are_denied_at_once_even_on_a_live_cell() -> None:
    # Seats are not one of the limits a wait may lift (the classification's own table).
    limits = _limits({GrantBound.SEATS: 0}, {GrantBound.SEATS: 3})

    assert waits_on(limits, is_live=True) is None


def test_a_passing_shortfall_beside_a_denied_one_is_denied_at_once() -> None:
    limits = _limits(
        {GrantBound.FREE_CORES: 0, GrantBound.SEATS: 0},
        {GrantBound.FREE_CORES: 8, GrantBound.SEATS: 3},
    )

    assert waits_on(limits, is_live=True) is None


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
