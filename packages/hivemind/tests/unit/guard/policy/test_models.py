"""Tests for hivemind.guard.policy.models: principals, requests, contexts and decisions.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/models.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.models for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell import AccessLevel, CombShieldLevel, RequestOrigin
from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.policy.models import (
    OPERATOR_ID,
    EscalationAction,
    PolicyContext,
    PolicyDecision,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
)
from hivemind.guard.policy.points import EnforcementPoint
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id

_CLOCK = FakeClock()


@pytest.mark.parametrize(
    ("kind", "id_kind"),
    [
        (PrincipalKind.QUEEN, IdKind.HIVE),
        (PrincipalKind.WARDEN, IdKind.WARDEN),
        (PrincipalKind.WORKER, IdKind.WORKER),
        (PrincipalKind.SWARM_DEVICE, IdKind.DEVICE),
        (PrincipalKind.CLIENT_DEVICE, IdKind.DEVICE),
    ],
)
def test_principal_accepts_the_id_kind_it_acts_as(kind: PrincipalKind, id_kind: IdKind) -> None:
    principal_id = new_id(id_kind, _CLOCK)

    principal = PrincipalRef(kind=kind, id=principal_id, role="drone")

    assert principal.id == principal_id


def test_principal_refuses_an_id_of_another_kind() -> None:
    with pytest.raises(ValidationError):
        PrincipalRef(kind=PrincipalKind.WORKER, id=new_id(IdKind.WARDEN, _CLOCK), role="drone")


def test_the_operator_is_always_the_human_literal() -> None:
    operator = PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role="operator")

    assert operator.id == "human"
    with pytest.raises(ValidationError, match="operator's principal id"):
        PrincipalRef(kind=PrincipalKind.OPERATOR, id=new_id(IdKind.HIVE, _CLOCK), role="operator")


@pytest.mark.parametrize("role", ["", "Drone", "guard-bee", "x" * 65])
def test_principal_refuses_a_role_that_is_not_a_policy_key(role: str) -> None:
    with pytest.raises(ValidationError):
        PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role=role)


def test_context_defaults_to_not_on_a_cell_and_not_a_task() -> None:
    context = PolicyContext()

    assert context.comb_shield is None
    assert context.access_level is None
    assert context.bound_tier is None
    assert context.origin is None


def test_request_round_trips_through_json() -> None:
    request = PolicyRequest(
        principal=PrincipalRef(
            kind=PrincipalKind.WORKER, id=new_id(IdKind.WORKER, _CLOCK), role="drone"
        ),
        point=EnforcementPoint.TOOL_INVOCATION,
        needed=Capability.parse("tool:http_get"),
        held=CapabilitySet.parse("tool:*", "net:*.example.com"),
        context=PolicyContext(
            comb_shield=CombShieldLevel.MEADOW,
            access_level=AccessLevel.SCRATCH,
            bound_tier=CombShieldLevel.MEADOW,
            origin=RequestOrigin.HUMAN,
        ),
    )

    assert PolicyRequest.model_validate_json(request.model_dump_json()) == request


def test_decision_round_trips_and_refuses_an_empty_rule() -> None:
    decision = PolicyDecision(
        allowed=False,
        rule="guard.not_held",
        reason="Worker w was refused tool:x at enforcement point tool_invocation: not held.",
        escalation=EscalationAction.ALARM,
    )

    assert PolicyDecision.model_validate_json(decision.model_dump_json()) == decision
    with pytest.raises(ValidationError):
        PolicyDecision(allowed=True, rule="", reason="r", escalation=EscalationAction.REFUSE)


def test_escalation_action_values_are_the_manifest_names() -> None:
    assert [action.value for action in EscalationAction] == ["refuse", "alarm", "ask_human"]
