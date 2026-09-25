"""Tests for hivemind.guard.policy.floors.chain: the floors' one order, and the first refusal wins.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/chain.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.floors.chain for the module under test.
"""

from __future__ import annotations

from builders.guard import make_request

from hivemind.cell import CombShieldLevel
from hivemind.guard.policy import PolicyContext, load_guard_policy
from hivemind.guard.policy.floors import (
    FLOORS,
    floor_refusal,
    hive_state_floor,
    inheritance_floor,
    initiation_floor,
    night_veil_floor,
)

_POLICY = load_guard_policy()


def test_the_hive_state_floor_runs_first_and_inheritance_last() -> None:
    assert (hive_state_floor, initiation_floor, night_veil_floor, inheritance_floor) == FLOORS


def test_when_two_floors_refuse_the_first_in_order_decides() -> None:
    # A Night Veil bee reaching the metadata service meets both floors: the state floor answers.
    context = PolicyContext(comb_shield=CombShieldLevel.NIGHT_VEIL)

    refusal = floor_refusal(make_request("net:169.254.169.254", context=context), _POLICY)

    assert refusal is not None
    assert refusal.rule == "guard.state_floor.loopback"


def test_no_floor_refuses_an_ordinary_action() -> None:
    assert floor_refusal(make_request("tool:read_file"), _POLICY) is None
