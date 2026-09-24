"""Tests for the Hive-state floor's own-name rule: a Cell's name for the Hive Stand is state too.

Inside a Virtual Cell the Hive Stand is reached by a name: its host-gateway alias, or, from a Night
Veil Cell, its onion service, which is never looked up there. `HiveState.own_host_names` names
them, and the floor refuses a bee `net` to any of them by name (and a `*.domain` scope that would
cover one), before any lookup could happen.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/hive_state.py (codingrules 5.1: split by feature
    from test_hive_state.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.hive_state for HiveState.own_host_names.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.guard import make_request

from hivemind.guard.policy import HiveState, evaluate
from hivemind.guard.policy.defaults import load_guard_policy

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_STATE = HiveState.of(own_host_names=("Host.Docker.Internal.", _ONION))
_POLICY = dataclasses.replace(load_guard_policy(), hive_state=_STATE)
_LOOPBACK = "guard.state_floor.loopback"


def _decide(needed: str) -> tuple[str, str]:
    """Decide `needed` for a Worker holding every host; return the rule and the reason."""
    decision = evaluate(make_request(needed, "net:*"), _POLICY)
    return decision.rule, decision.reason


def test_the_names_are_kept_normalised() -> None:
    assert _STATE.own_host_names == frozenset({"host.docker.internal", _ONION})


@pytest.mark.parametrize(
    "needed",
    [
        "net:host.docker.internal",
        "net:HOST.docker.internal",
        f"net:{_ONION}",
        "net:*.docker.internal",  # Covers the alias.
        "net:*.internal",  # Covers it too, one label further out.
    ],
)
def test_a_bee_is_refused_every_name_the_hive_stand_is_reached_by(needed: str) -> None:
    rule, reason = _decide(needed)

    assert rule == _LOOPBACK
    assert "the Hive Stand's own name from here" in reason


@pytest.mark.parametrize(
    "needed", ["net:docker.internal", "net:other.docker.internal", "net:*.example.com"]
)
def test_a_name_that_is_not_one_of_them_is_left_to_the_held_set(needed: str) -> None:
    assert _decide(needed)[0] == "guard.held"


def test_with_no_names_stated_nothing_is_refused_by_name() -> None:
    policy = dataclasses.replace(load_guard_policy(), hive_state=HiveState())

    decision = evaluate(make_request("net:host.docker.internal", "net:*"), policy)

    assert decision.allowed
