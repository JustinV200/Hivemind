"""The Guard's policy engine: who may do what, where, decided by a pure function with a reason.

ADR-0031 splits authorisation into data and one pure decision. The data is a `GuardPolicy`:
each policy role's default capability set, a hive-wide deny list and what a denial at each
enforcement point escalates to, loaded from the shipped `policy.toml` (or an operator's file) with
the manifest's `[guard]` table on top (`defaults`). The decision is `evaluate` (`evaluate`), over a
`PolicyRequest` naming the principal, the `EnforcementPoint` (`points`), the needed capability,
the held set and the context (`models`). `roles` builds a principal's set from its role: a
Warden's (its role default narrowed to its Cell's access level, less the deny list), a Worker
role's default, a device's proposed approval. Everything in this package is pure; recording a
denial on the trail is `hivemind.guard.enforcer`'s job.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by the composition roots
    (`hivemind.cli.compose.deps`, `hivemind.cli.in_cell.deps`); read by the Warden (its own set
    and each Worker's role default) and by every enforcement point through the enforcer. Calls
    into `hivemind.guard.capabilities`, `hivemind.guard.access`, `hivemind.cell` (the tier enums),
    `hivemind.manifest.schema.guard` (the `[guard]` shapes) and waggle (ids, WorkerRole).

Key invariants:
    - Pure: no module here performs I/O except `defaults.load_guard_policy`, which reads the
      policy file once, at start.
    - Nothing here widens a set, and only holding the capability ever turns a request into an
      allow.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the decision.
    - hivemind.guard.enforcer for the effectful adapter around `evaluate`.
    - hivemind.guard.defaults for the shipped policy.toml.

Public API:
    - EnforcementPoint: every named place an action is authorised (points).
    - PrincipalKind, PrincipalRef, OPERATOR_ID, PolicyContext, PolicyRequest, EscalationAction,
      PolicyDecision: the request and the answer (models).
    - GuardPolicy, RoleDefaults, POLICY_ROLES, WARDEN_ROLE, DEVICE_ROLE: the checked policy
      (table).
    - load_guard_policy, DEFAULT_POLICY_FILENAME: build it from a file and `[guard]` (defaults).
    - evaluate, HELD_RULE, NOT_HELD_RULE, DENY_LIST_RULE, ACCESS_LEVEL_RULE, TIER_FLOOR_RULE: the
      decision and its stable rule ids (evaluate).
    - role_set, warden_set, proposed_set, worker_role_name: a principal's set from its role
      (roles).
"""

from hivemind.guard.policy.defaults import DEFAULT_POLICY_FILENAME, load_guard_policy
from hivemind.guard.policy.evaluate import (
    ACCESS_LEVEL_RULE,
    DENY_LIST_RULE,
    HELD_RULE,
    NOT_HELD_RULE,
    TIER_FLOOR_RULE,
    evaluate,
)
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
from hivemind.guard.policy.roles import proposed_set, role_set, warden_set, worker_role_name
from hivemind.guard.policy.table import (
    DEVICE_ROLE,
    POLICY_ROLES,
    WARDEN_ROLE,
    GuardPolicy,
    RoleDefaults,
)

__all__ = [
    "ACCESS_LEVEL_RULE",
    "DEFAULT_POLICY_FILENAME",
    "DENY_LIST_RULE",
    "DEVICE_ROLE",
    "HELD_RULE",
    "NOT_HELD_RULE",
    "OPERATOR_ID",
    "POLICY_ROLES",
    "TIER_FLOOR_RULE",
    "WARDEN_ROLE",
    "EnforcementPoint",
    "EscalationAction",
    "GuardPolicy",
    "PolicyContext",
    "PolicyDecision",
    "PolicyRequest",
    "PrincipalKind",
    "PrincipalRef",
    "RoleDefaults",
    "evaluate",
    "load_guard_policy",
    "proposed_set",
    "role_set",
    "warden_set",
    "worker_role_name",
]
