"""Decide one action: the Guard's pure policy function, returning the rule that decided and why.

`evaluate` is ADR-0031's "policy is a pure function with a reason": given who is acting, at which
enforcement point, the capability the action needs, the set the principal holds and where the
action happens, it answers allow or deny with a stable rule id, a sentence a human can read on
the trail, and what a denial escalates to (the policy's per-point table, `REFUSE` by default).
Rules run cheapest and hardest first, and only holding the capability can turn anything into an
allow: the tier floors, then the access-level ceiling, then the hive-wide deny list, each of
which can only refuse, and last the held set. The floors are an explicit hook that roadmap step
10.3a fills; nothing here invents one. `refusal` (roadmap step 10.3) builds the same shape of
decision for a refusal an enforcement point reaches on its own, when the principal holds the
capability but the action's target lies outside what it may touch with it (a grant another Warden
holds): the rule is a `guard.scope.<why>` id and the reason reads like every other one.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.guard.enforcer.
    Enforcer.check`, the one adapter every enforcement point goes through (roadmap step 10.3).
    Calls into `hivemind.guard.access` (`admits`) and this package's `models` and `table`; no
    I/O, no clock, no trail: the enforcer records a denial, this module only decides it.

Key invariants:
    - Pure and total: the same request and policy always give the same decision, and every
      request gets one.
    - An allow always comes from `guard.held`: no earlier rule can allow, and a principal that
      does not hold the needed capability is always refused.
    - A decision's reason names the principal (kind, id, role), the point and the capability,
      never any content the action carried.
    - `refusal` only ever refuses: it cannot allow, so the invariant above still holds.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Policy is a pure
      function with a reason" and "Floors hold whatever a set says".
    - hivemind.guard.enforcer for the adapter that records a denial before returning it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hivemind.guard.access import admits
from hivemind.guard.policy.models import (
    EscalationAction,
    PolicyDecision,
    PolicyRequest,
    PrincipalRef,
)
from hivemind.guard.policy.table import GuardPolicy

HELD_RULE = "guard.held"  # The only rule that allows: the principal holds what the action needs.
NOT_HELD_RULE = "guard.not_held"  # Refused because the held set does not cover the need.
DENY_LIST_RULE = "guard.deny_list"  # Refused because the hive-wide deny list covers the need.
ACCESS_LEVEL_RULE = "guard.access_level"  # Prefix: `.read_only`, `.scratch`: the Cell's level.
TIER_FLOOR_RULE = "guard.tier_floor"  # Prefix for step 10.3a's floors: `.night_veil`, ...
SCOPE_RULE = "guard.scope"  # Prefix: `.grant_holder`, ...: held, but not over this target.

__all__ = [
    "ACCESS_LEVEL_RULE",
    "DENY_LIST_RULE",
    "HELD_RULE",
    "NOT_HELD_RULE",
    "SCOPE_RULE",
    "TIER_FLOOR_RULE",
    "evaluate",
    "refusal",
]


@dataclass(frozen=True, slots=True)
class _Verdict:
    """One rule's verdict: its stable id and the clause saying why it decided as it did."""

    rule: str  # The stable rule id recorded on the decision and the trail.
    why: str  # The end of the reason sentence: why this rule allows or refuses the action.


def evaluate(request: PolicyRequest, policy: GuardPolicy) -> PolicyDecision:
    """Decide whether `request`'s principal may take its action, and why.

    Args:
        request: Who is acting, at which point, needing what, holding what, and where.
        policy: The Guard policy: the deny list and the per-point escalation table.

    Returns:
        A PolicyDecision: allowed by `guard.held` when the held set covers the need and no
        refusing rule applies; otherwise refused by the first rule that applies, in ADR-0031's
        order (a tier floor, the access-level ceiling, the deny list), or by `guard.not_held`.
        Its escalation is the point's configured action, REFUSE when it has none.

    Example:
        >>> evaluate(request, policy).rule  # doctest: +SKIP
        'guard.held'
    """
    escalation = policy.escalation_for(request.point)
    # The rules that can only refuse, cheapest and hardest first; the first to apply decides.
    for rule in _REFUSING_RULES:
        refusal = rule(request, policy)
        if refusal is not None:
            return _decision(request, False, refusal, escalation)
    # Only now can the answer be yes, and only because the principal holds what it needs.
    if request.held.allows(request.needed):
        return _decision(
            request, True, _Verdict(HELD_RULE, "its capability set holds it"), escalation
        )
    return _decision(
        request, False, _Verdict(NOT_HELD_RULE, "its capability set does not hold it"), escalation
    )


def refusal(request: PolicyRequest, policy: GuardPolicy, scope: str, why: str) -> PolicyDecision:
    """Build the refusal for an action whose target lies outside what the principal may touch.

    Some enforcement points refuse for a reason the held set cannot express: a Warden holds
    `forage:request`, but only for a grant it holds itself. The point decides that on its own
    and calls this (through `hivemind.guard.enforcer.Enforcer.refuse`) so the refusal still has a
    stable rule id, a reason in the same sentence shape as every other, and the point's escalation.

    Args:
        request: Who acted, at which point, needing what, holding what, and where.
        policy: The Guard policy; only its per-point escalation table is read.
        scope: What the target lay outside of, a lowercase snake_case word (`grant_holder`);
            the rule becomes `guard.scope.<scope>`.
        why: The end of the reason sentence: why the target is out of reach, naming ids only.

    Returns:
        A refused PolicyDecision; never an allow.
    """
    verdict = _Verdict(f"{SCOPE_RULE}.{scope}", why)
    return _decision(request, False, verdict, policy.escalation_for(request.point))


def _tier_floor(request: PolicyRequest, policy: GuardPolicy) -> _Verdict | None:
    """Refuse an action a floor forbids whatever the principal holds; none exist yet.

    Empty on purpose until roadmap step 10.3a. The floors ADR-0031 names (the Hive's own state
    paths; Night Veil's Virtual-only placement, local-only slots, Tor-only link, refusal of `c2`
    Honey and of the location families; Night Veil initiated only by a human; tier inheritance)
    read `request.context` (the Cell's tier, the task's bound or requested tier, the origin),
    never the shape of the held set, and each will return a `guard.tier_floor.<floor>` refusal
    from here. Returning None applies no floor.
    """
    del request, policy  # Read by the floors step 10.3a adds; nothing reads them until then.
    return None


def _access_level_ceiling(request: PolicyRequest, policy: GuardPolicy) -> _Verdict | None:
    """Refuse a Cell effect the Cell's access level never permits, whatever the held set says."""
    del policy  # The ceiling is fixed data in hivemind.guard.access, not configuration.
    level = request.context.access_level
    family = request.needed.family
    # Not on a Cell, or a family this level permits at some scope: the held set, narrowed with
    # the real scratch root when it was built, decides the exact scope later.
    if level is None or admits(level, family):
        return None
    return _Verdict(
        rule=f"{ACCESS_LEVEL_RULE}.{level.name.lower()}",
        why=f"a {level.name} Cell never permits {family.value}",
    )


def _deny_list(request: PolicyRequest, policy: GuardPolicy) -> _Verdict | None:
    """Refuse anything the hive-wide deny list covers, whatever the principal holds."""
    if policy.deny.allows(request.needed):
        return _Verdict(DENY_LIST_RULE, "the hive-wide deny list covers it")
    return None


# ADR-0031's order for the rules that can only refuse: floors, the access ceiling, the deny list.
_REFUSING_RULES: tuple[Callable[[PolicyRequest, GuardPolicy], _Verdict | None], ...] = (
    _tier_floor,
    _access_level_ceiling,
    _deny_list,
)


def _decision(
    request: PolicyRequest, allowed: bool, verdict: _Verdict, escalation: EscalationAction
) -> PolicyDecision:
    """Build the decision, with a reason naming the principal, the point and the capability."""
    verb = "allowed" if allowed else "refused"
    reason = (
        f"{_describe(request.principal)} was {verb} {request.needed} at enforcement point "
        f"{request.point.value}: {verdict.why}."
    )
    return PolicyDecision(allowed=allowed, rule=verdict.rule, reason=reason, escalation=escalation)


def _describe(principal: PrincipalRef) -> str:
    """Name a principal for a reason sentence: its kind, its id and its policy role."""
    kind = principal.kind.value.replace("_", " ").capitalize()  # "client_device" -> "Client device"
    return f"{kind} {principal.id} (role {principal.role})"
