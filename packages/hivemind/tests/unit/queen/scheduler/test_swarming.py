"""Unit tests for the Swarming policy: every bound caps fresh Cells, and unset bounds cap none.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.scheduler.swarming for the policy under test.
"""

from __future__ import annotations

import pytest

from hivemind.queen.scheduler import (
    BackendLimit,
    BackendLoad,
    SwarmingPolicy,
    SwarmView,
    plan_swarm,
)

_DOCKER = "docker"
_CLOUD = "cloud"


def _view(
    docker: tuple[int, int] = (0, 0),
    cloud: tuple[int, int] = (0, 0),
    stand_room: int | None = None,
) -> SwarmView:
    """Build a two-backend view; each tuple is (held, provisioning)."""
    return SwarmView(
        backends=(BackendLoad(_DOCKER, *docker), BackendLoad(_CLOUD, *cloud)),
        stand_room=stand_room,
    )


_PRICED = {
    _DOCKER: BackendLimit(draws_on_hive_stand=True),
    _CLOUD: BackendLimit(max_cells=3, cost_per_hour_usd=0.5),
}


@pytest.mark.parametrize(
    ("view", "policy", "docker", "cloud", "reason"),
    [
        # The default policy binds nothing.
        (_view((7, 2)), SwarmingPolicy(), None, None, None),
        # max_parallel counts held and in-flight Cells across every backend.
        (_view((1, 1), (1, 0)), SwarmingPolicy(max_parallel=5), 2, 2, None),
        (_view((1, 1), (1, 0)), SwarmingPolicy(max_parallel=3), 0, 0, "max_parallel 3 reached"),
        # A backend's own max_cells binds it alone; the other backend is untouched.
        (_view((4, 0), (2, 1)), SwarmingPolicy(backends=_PRICED), None, 0, "max_cells 3 reached"),
        # Cost: two cloud Cells burn 1.0 of a 1.75 cap, room for one more at 0.5; docker is free.
        (
            _view((5, 0), (1, 1)),
            SwarmingPolicy(max_cost_per_hour_usd=1.75, backends=_PRICED),
            None,
            1,
            None,
        ),
        (
            _view((0, 0), (2, 0)),
            SwarmingPolicy(max_cost_per_hour_usd=1.0, backends=_PRICED),
            None,
            0,
            "max_cost_per_hour_usd 1.0 reached",
        ),
        # The Hive Stand's room caps only a backend that draws on it.
        (_view(stand_room=0), SwarmingPolicy(backends=_PRICED), 0, 3, "no room for another"),
        (_view(stand_room=2), SwarmingPolicy(backends=_PRICED), 2, 3, None),
        # A Cell still being made on a drawing backend uses no memory yet: take it off the room.
        (
            _view((0, 1), stand_room=1),
            SwarmingPolicy(backends=_PRICED),
            0,
            3,
            "no room for another",
        ),
    ],
)
def test_plan_swarm_takes_the_smallest_bound_that_is_set(
    view: SwarmView,
    policy: SwarmingPolicy,
    docker: int | None,
    cloud: int | None,
    reason: str | None,
) -> None:
    """Each backend gets the smallest set bound; a zero carries the reason naming that bound."""
    plan = plan_swarm(view, policy)
    assert plan.allowance == {_DOCKER: docker, _CLOUD: cloud}
    zeroed = [plan.reasons[name] for name, room in plan.allowance.items() if room == 0]
    assert set(plan.reasons) == {name for name, room in plan.allowance.items() if room == 0}
    if reason is not None:
        assert zeroed
        assert all(text.startswith("swarming: ") and reason in text for text in zeroed)


def test_an_overspent_cap_never_gives_a_negative_allowance() -> None:
    """Cells held past a lowered cap leave an allowance of zero, never below."""
    plan = plan_swarm(_view((6, 0), (3, 0)), SwarmingPolicy(max_parallel=4, backends=_PRICED))
    assert plan.allowance == {_DOCKER: 0, _CLOUD: 0}
