"""Tests for hivemind.guard.policy.floors.human_only: only the human passes the Absconding point.

Roadmap step 13.4 and codingrules 15: Absconding is never callable by the Queen, a Warden, a
Worker, a device or a tool. Each non-human principal is given `abscond` in its held set, as a
hostile `[guard.roles.<role>]` override would, so any refusal here is the floor's, not the set's.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/floors/human_only.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.floors.human_only for the module under test.
"""

from __future__ import annotations

import pytest
from builders.guard import make_request

from hivemind.guard.policy import EnforcementPoint, PrincipalKind, evaluate, role_set
from hivemind.guard.policy.defaults import load_guard_policy

_POLICY = load_guard_policy()
_ABSCOND = EnforcementPoint.ABSCONDING
# Each human-only point with the capability it needs: Absconding, and Supersedure (ADR-0065).
_HUMAN_ONLY = [
    (EnforcementPoint.ABSCONDING, "abscond"),
    (EnforcementPoint.SUPERSEDURE, "supersede"),
]


@pytest.mark.parametrize(
    "kind", [kind for kind in PrincipalKind if kind is not PrincipalKind.OPERATOR]
)
@pytest.mark.parametrize(("point", "needed"), _HUMAN_ONLY)
def test_every_non_human_principal_is_refused_even_holding_the_capability(
    kind: PrincipalKind, point: EnforcementPoint, needed: str
) -> None:
    decision = evaluate(make_request(needed, needed, kind=kind, point=point), _POLICY)

    assert not decision.allowed
    assert decision.rule == "guard.state_floor.human_only"


@pytest.mark.parametrize(
    ("held", "rule"),
    [
        (None, "guard.held"),  # The operator role's own set holds `abscond`.
        ((), "guard.not_held"),  # A policy that dropped it from the operator refuses her too.
    ],
)
def test_the_human_passes_only_holding_abscond(held: tuple[str, ...] | None, rule: str) -> None:
    operator = tuple(str(c) for c in role_set(_POLICY, "operator")) if held is None else held
    request = make_request("abscond", *operator, kind=PrincipalKind.OPERATOR, point=_ABSCOND)

    assert evaluate(request, _POLICY).rule == rule
