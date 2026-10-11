"""Tests for hivemind.llm.routing.decision: RoutingReason.render and the decision values.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/decision.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.routing.decision for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.forage import Effort
from hivemind.llm.routing import (
    ForageShortfall,
    PassedOver,
    PassOverCause,
    RoutingDecision,
    RoutingReason,
)
from waggle.messages.base import MAX_REASON_CHARS


def _reason(**overrides: object) -> RoutingReason:
    fields: dict[str, object] = {
        "floor": 3,
        "considered": 4,
        "eligible": 2,
        "chosen_grade": 4,
        "local": True,
        "planned": False,
        "fell_back": False,
    }
    fields.update(overrides)
    return RoutingReason(**fields)  # type: ignore[arg-type]


def test_render_states_every_factor_that_decided_the_choice() -> None:
    text = _reason().render()

    assert text == (
        "2 of 4 binding(s) eligible at floor 3; chosen grade=4; local=True; planned=False; "
        "fell_back_to_primary=False."
    )


def test_render_says_unknown_when_the_chosen_source_has_no_grade() -> None:
    assert "chosen grade=unknown" in _reason(chosen_grade=None).render()


def test_render_never_exceeds_the_reason_bound() -> None:
    # A pathological value cannot overflow the bound a trail event carries.
    assert len(_reason(considered=10**2000).render()) <= MAX_REASON_CHARS


def test_pass_over_causes_name_the_five_ineligible_and_two_demoting_reasons() -> None:
    assert {cause.name for cause in PassOverCause} == {
        "GRADE_BELOW_FLOOR",
        "CAPABILITY_MISSING",
        "TOO_FAR",
        "NO_SEAT",
        "OVER_COST",
        "NOT_LOCAL_OFFLINE",
        "NOT_ON_MAP",
    }


def test_decision_values_are_frozen() -> None:
    decision = RoutingDecision(
        key="worker",
        order=("worker",),
        effort=Effort.LOW,
        reason=_reason(),
        passed_over=(PassedOver("b", PassOverCause.NO_SEAT),),
        forage_shortfall=ForageShortfall(source_id="src_a", seats_wanted=1),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.key = "other"  # type: ignore[misc]
