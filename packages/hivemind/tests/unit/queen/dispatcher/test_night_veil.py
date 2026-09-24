"""Tests for hivemind.queen.dispatcher.night_veil: the facts the Night Veil floors are given.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/night_veil.py (codingrules section 3). The dispatch
    flows through it are covered end to end by tests/unit/queen/test_queen_dispatch_night_veil.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.night_veil for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.human import make_goal_request
from builders.queen import make_queen_deps
from builders.tasks import make_task

from hivemind.brood_chamber import Task
from hivemind.cell import CombShieldLevel, Isolation, RequestOrigin, TaskNeeds
from hivemind.guard.policy import ControlLink, GoalRequestFacts
from hivemind.hive import NetworkPolicy
from hivemind.queen.dispatcher.night_veil import control_link, goal_request_facts, tier_context
from hivemind.queen.intake import new_goal_request_id, receive
from hivemind.queen.placement import NightVeilConstraints, PlacementPolicy

_TOR = "socks5h://127.0.0.1:9050"
_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.


def _policy(address: str, socks: str = _TOR) -> PlacementPolicy:
    """A placement policy whose Night Veil profile names `address` and `socks`."""
    profile = NightVeilConstraints(
        required_network_policy=NetworkPolicy.VPN_TOR,
        hive_stand_onion_address=address,
        socks_proxy_url=socks,
        locale_profile="C.UTF-8",
    )
    return PlacementPolicy(night_veil=profile)


def _task(tier: CombShieldLevel, request_id: str | None = None) -> Task:
    """A PENDING task asking for `tier`, citing `request_id`."""
    isolation = Isolation.REQUIRED if tier is CombShieldLevel.NIGHT_VEIL else Isolation.PREFERRED
    task = make_task()
    spec = task.spec.model_copy(
        update={
            "needs": TaskNeeds(comb_shield=tier, isolation=isolation),
            "origin": RequestOrigin.HUMAN,
            "goal_request_id": request_id,
        }
    )
    return task.model_copy(update={"spec": spec})


@pytest.mark.parametrize(
    "address",
    [
        _ONION,
        f"{_ONION}:8710",
        f"ws://{_ONION}:8710/waggle",
    ],
)
def test_the_control_link_names_the_hidden_service_host_however_it_is_written(
    address: str,
) -> None:
    link = control_link(_policy(address))

    assert link == ControlLink(host=_ONION, socks_proxy_url=_TOR)


def test_an_unset_profile_or_address_states_no_link_and_an_unset_proxy_states_none() -> None:
    assert control_link(PlacementPolicy()) is None
    assert control_link(_policy("")) is None
    assert control_link(_policy("x.onion", socks="")) == ControlLink(host="x.onion")


async def test_goal_request_facts_come_from_the_row_the_task_cites() -> None:
    deps, _link, _end = make_queen_deps()
    request = await receive(
        deps, make_goal_request(deps.clock, comb_shield=CombShieldLevel.NIGHT_VEIL)
    )

    facts = await goal_request_facts(deps, _task(CombShieldLevel.NIGHT_VEIL, request.id))

    assert facts == GoalRequestFacts(
        origin=RequestOrigin.HUMAN, comb_shield=CombShieldLevel.NIGHT_VEIL
    )


async def test_no_facts_for_a_task_citing_no_request_or_a_row_that_is_gone() -> None:
    deps, _link, _end = make_queen_deps()

    assert await goal_request_facts(deps, _task(CombShieldLevel.NIGHT_VEIL)) is None
    missing = _task(CombShieldLevel.NIGHT_VEIL, new_goal_request_id(deps.clock))
    assert await goal_request_facts(deps, missing) is None


async def test_the_night_veil_facts_are_added_only_under_night_veil() -> None:
    base, _link, _end = make_queen_deps()
    deps = dataclasses.replace(base, placement_policy=_policy("x.onion"))

    meadow = await tier_context(deps, _task(CombShieldLevel.MEADOW))
    armed = await tier_context(deps, _task(CombShieldLevel.MEADOW), CombShieldLevel.NIGHT_VEIL)

    assert meadow.control_link is None and meadow.goal_request is None
    assert armed.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert armed.control_link == ControlLink(host="x.onion", socks_proxy_url=_TOR)
