"""Tests for hivemind.guard.policy.evaluate: the rule order, the reason and the escalation.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/evaluate.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.evaluate for the module under test.
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the rule order.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.cell import AccessLevel, CombShieldLevel, RequestOrigin
from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.policy.defaults import load_guard_policy
from hivemind.guard.policy.evaluate import (
    ACCESS_LEVEL_RULE,
    DENY_LIST_RULE,
    HELD_RULE,
    NOT_HELD_RULE,
    SCOPE_RULE,
    evaluate,
    refusal,
)
from hivemind.guard.policy.models import (
    OPERATOR_ID,
    EscalationAction,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
)
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.manifest import GuardSection
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id

_CLOCK = FakeClock()
_WORKER = PrincipalRef(kind=PrincipalKind.WORKER, id=new_id(IdKind.WORKER, _CLOCK), role="drone")
_POLICY = load_guard_policy()  # The shipped policy: nothing denied, every point refuses.
# codingrules 14.3: a generous, deterministic example budget and no per-test deadline.
_SETTINGS = settings(max_examples=300, deadline=None)


def _request(
    needed: str,
    *held: str,
    context: PolicyContext | None = None,
    principal: PrincipalRef = _WORKER,
) -> PolicyRequest:
    """Build a tool-invocation request for `needed` by `principal`, holding `held`."""
    return PolicyRequest(
        principal=principal,
        point=EnforcementPoint.TOOL_INVOCATION,
        needed=Capability.parse(needed),
        held=CapabilitySet.parse(*held),
        context=context or PolicyContext(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# The held set: the only rule that allows
# ──────────────────────────────────────────────────────────────────────────────


def test_a_held_capability_is_allowed_by_the_held_rule() -> None:
    decision = evaluate(_request("tool:http_get", "tool:*"), _POLICY)

    assert decision.allowed is True
    assert decision.rule == HELD_RULE


def test_a_capability_not_held_is_refused_with_a_reason_naming_who_where_and_what() -> None:
    decision = evaluate(_request("net:api.example.com", "tool:*"), _POLICY)

    assert decision.allowed is False
    assert decision.rule == NOT_HELD_RULE
    assert decision.reason == (
        f"Worker {_WORKER.id} (role drone) was refused net:api.example.com at enforcement point "
        "tool_invocation: its capability set does not hold it."
    )


def test_the_operator_is_named_as_the_human_in_a_reason() -> None:
    operator = PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role="operator")

    decision = evaluate(_request("supersede", "supersede", principal=operator), _POLICY)

    assert decision.reason.startswith("Operator human (role operator) was allowed supersede")


# ──────────────────────────────────────────────────────────────────────────────
# The deny list: refuses whatever is held
# ──────────────────────────────────────────────────────────────────────────────


def test_the_deny_list_refuses_a_capability_even_when_it_is_held() -> None:
    policy = load_guard_policy(None, GuardSection(deny=("net:*.evil.example",)))

    decision = evaluate(_request("net:api.evil.example", "net:*"), policy)

    assert decision.allowed is False
    assert decision.rule == DENY_LIST_RULE
    assert "deny list" in decision.reason


def test_the_deny_list_leaves_what_it_does_not_cover_to_the_held_set() -> None:
    policy = load_guard_policy(None, GuardSection(deny=("net:*.evil.example",)))

    assert evaluate(_request("net:api.example.com", "net:*"), policy).allowed is True


# ──────────────────────────────────────────────────────────────────────────────
# The access-level ceiling: refuses a Cell effect the level never permits
# ──────────────────────────────────────────────────────────────────────────────


def test_a_read_only_cell_refuses_exec_even_when_it_is_held() -> None:
    context = PolicyContext(access_level=AccessLevel.READ_ONLY)

    decision = evaluate(_request("exec:ls", "exec:*", context=context), _POLICY)

    assert decision.allowed is False
    assert decision.rule == f"{ACCESS_LEVEL_RULE}.read_only"
    assert "a READ_ONLY Cell never permits exec" in decision.reason


def test_a_scratch_cell_refuses_the_network_but_leaves_write_scopes_to_the_held_set() -> None:
    context = PolicyContext(access_level=AccessLevel.SCRATCH)

    net = evaluate(_request("net:a.example", "net:*", context=context), _POLICY)
    inside = evaluate(_request("fs:write:/s/x", "fs:write:/s/**", context=context), _POLICY)
    outside = evaluate(_request("fs:write:/etc/x", "fs:write:/s/**", context=context), _POLICY)

    assert net.rule == f"{ACCESS_LEVEL_RULE}.scratch"
    assert inside.allowed is True
    assert outside.rule == NOT_HELD_RULE


def test_the_access_level_never_touches_a_family_it_does_not_govern() -> None:
    context = PolicyContext(access_level=AccessLevel.READ_ONLY)

    assert evaluate(_request("tool:x", "tool:*", context=context), _POLICY).allowed is True
    assert evaluate(_request("spend:5", "spend:*", context=context), _POLICY).allowed is True


def test_off_a_cell_no_access_level_applies() -> None:
    assert evaluate(_request("exec:ls", "exec:*"), _POLICY).allowed is True


def test_the_access_level_ceiling_runs_before_the_deny_list() -> None:
    policy = load_guard_policy(None, GuardSection(deny=("exec:*",)))
    context = PolicyContext(access_level=AccessLevel.READ_ONLY)

    decision = evaluate(_request("exec:ls", "exec:*", context=context), policy)

    assert decision.rule == f"{ACCESS_LEVEL_RULE}.read_only"


def test_no_tier_floor_exists_yet() -> None:
    # Roadmap step 10.3a fills the floor hook; until then a Night Veil context changes nothing.
    context = PolicyContext(
        comb_shield=CombShieldLevel.NIGHT_VEIL,
        bound_tier=CombShieldLevel.NIGHT_VEIL,
        origin=RequestOrigin.QUEEN,
    )

    assert evaluate(_request("geo:city", "geo:*", context=context), _POLICY).allowed is True


# ──────────────────────────────────────────────────────────────────────────────
# Escalation: the point's action, REFUSE by default
# ──────────────────────────────────────────────────────────────────────────────


def test_escalation_is_the_points_configured_action() -> None:
    policy = load_guard_policy(None, GuardSection(escalation={"tool_invocation": "ask_human"}))

    refused = evaluate(_request("net:a.example"), policy)
    allowed = evaluate(_request("tool:x", "tool:*"), policy)

    assert refused.escalation is EscalationAction.ASK_HUMAN
    assert allowed.escalation is EscalationAction.ASK_HUMAN  # Carried, acted on only if refused.


def test_escalation_defaults_to_refuse() -> None:
    assert evaluate(_request("net:a.example"), _POLICY).escalation is EscalationAction.REFUSE


# ──────────────────────────────────────────────────────────────────────────────
# Property: only holding the capability ever turns a request into an allow
# ──────────────────────────────────────────────────────────────────────────────

_POOL = [
    "exec:*",
    "exec:ls",
    "fs:write:**",
    "fs:write:/s/**",
    "net:*",
    "net:*.example.com",
    "net:api.example.com",
    "tool:*",
    "tool:x",
    "llm:worker",
    "honey:clearance:c1",
]
_DENY_POLICY = load_guard_policy(None, GuardSection(deny=("net:*.example.com", "tool:x")))


@given(
    needed=st.sampled_from(_POOL),
    held=st.lists(st.sampled_from(_POOL), max_size=5),
    level=st.one_of(st.none(), st.sampled_from(list(AccessLevel))),
    denying=st.booleans(),
)
@_SETTINGS
def test_an_allow_always_means_held_undenied_and_admitted(
    needed: str, held: list[str], level: AccessLevel | None, denying: bool
) -> None:
    policy = _DENY_POLICY if denying else _POLICY
    request = _request(needed, *held, context=PolicyContext(access_level=level))

    decision = evaluate(request, policy)

    # Nothing but holding the capability turns a request into an allow (ADR-0031).
    assert decision.allowed is (decision.rule == HELD_RULE)
    if decision.allowed:
        assert request.held.allows(request.needed)
        assert not policy.deny.allows(request.needed)


def test_refusal_names_the_scope_rule_and_the_points_escalation() -> None:
    # Roadmap step 10.3: a point's own out-of-reach refusal; held, yet refused all the same.
    policy = load_guard_policy(None, GuardSection(escalation={"tool_invocation": "alarm"}))
    request = _request("tool:read_file", "tool:*")

    decision = refusal(request, policy, "binding_key", "no [llm.slots] row serves 'x'")

    assert decision.allowed is False
    assert decision.rule == f"{SCOPE_RULE}.binding_key"
    assert decision.reason.endswith("no [llm.slots] row serves 'x'.")
    assert decision.escalation is EscalationAction.ALARM
