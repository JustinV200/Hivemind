"""Tests for hivemind.guard.policy.facts: the control link and goal request facts floors read.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/facts.py (codingrules section 3): every boundary model has
    a round-trip test and a rejection test (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.facts for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell import CombShieldLevel, RequestOrigin
from hivemind.guard.policy import ControlLink, GoalRequestFacts


def test_a_control_link_round_trips_and_defaults_to_no_proxy() -> None:
    link = ControlLink(host="abc.onion", socks_proxy_url="socks5h://127.0.0.1:9050")

    assert ControlLink.model_validate_json(link.model_dump_json()) == link
    assert ControlLink(host="hive.example").socks_proxy_url is None


@pytest.mark.parametrize("fields", [{"host": ""}, {"host": "x", "extra": 1}])
def test_a_control_link_refuses_an_empty_host_or_an_unknown_field(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ControlLink.model_validate(fields)


def test_goal_request_facts_round_trip_and_default_to_no_tier() -> None:
    facts = GoalRequestFacts(origin=RequestOrigin.HUMAN, comb_shield=CombShieldLevel.NIGHT_VEIL)

    assert GoalRequestFacts.model_validate_json(facts.model_dump_json()) == facts
    assert GoalRequestFacts(origin=RequestOrigin.QUEEN).comb_shield is None


def test_goal_request_facts_refuse_an_unknown_origin() -> None:
    with pytest.raises(ValidationError):
        GoalRequestFacts.model_validate({"origin": "SOMEONE"})
