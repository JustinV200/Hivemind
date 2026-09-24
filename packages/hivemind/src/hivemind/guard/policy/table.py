"""Define GuardPolicy: every role's default set, the hive-wide deny list and the escalation table.

A `GuardPolicy` is the Guard's configuration once it has been read and checked
(`hivemind.guard.policy.defaults.load_guard_policy`): for each policy role (the Queen, the
operator, a Warden, each Worker role, an enrolled client device and a Swarm device) the default
capability set that role starts from, the capabilities no one may exercise whatever they hold
(the deny list), and what a denial at each enforcement point escalates to. A role's set may name
the `{scratch}` placeholder, which only a lease can fill, so a role keeps those entries as
validated strings and `hivemind.guard.policy.roles.role_set` substitutes them per lease; every
other entry is already a parsed `Capability`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by `hivemind.guard.policy.
    defaults`; held by a composition root and by `hivemind.wardens.deps.WardenDeps.guard`; read
    by `hivemind.guard.policy.roles` and `.evaluate`. Calls into `hivemind.guard.capabilities`,
    `.models` and `.points`.

Key invariants:
    - Frozen: a policy is built once at start and never changes while the Hive runs; its mappings
      are read-only views.
    - Its roles are exactly `POLICY_ROLES`, and only the `device` role carries a `proposed` set,
      never wider than its `allow` (the loader guarantees both).

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Two roots, and sets
      only narrow below them".
    - hivemind.guard.defaults for the shipped policy.toml this is usually built from.
    - hivemind.guard.policy.roles for role_set and warden_set, which build a principal's set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.guard.capabilities import CapabilitySet
from hivemind.guard.policy.models import EscalationAction
from hivemind.guard.policy.points import EnforcementPoint
from waggle.messages.task import WorkerRole

QUEEN_ROLE = "queen"  # The orchestrator's own root set (roadmap step 10.3 reads it at her points).
WARDEN_ROLE = "warden"  # The role every Warden's set starts from.
DEVICE_ROLE = "device"  # An enrolled client device: the only role with a `proposed` set.
# Every role a policy defines: the two roots, the Warden, one per Worker role (the lowercase wire
# WorkerRole name, as [placement.roles] and [forage.roles] key them), and the two device kinds.
POLICY_ROLES: frozenset[str] = frozenset(
    {"operator", QUEEN_ROLE, WARDEN_ROLE, DEVICE_ROLE, "swarm_device"}
    | {role.name.lower() for role in WorkerRole}
)

__all__ = [
    "DEVICE_ROLE",
    "POLICY_ROLES",
    "QUEEN_ROLE",
    "WARDEN_ROLE",
    "GuardPolicy",
    "RoleDefaults",
]


@dataclass(frozen=True, slots=True)
class RoleDefaults:
    """One role's default set, as the policy holds it before a lease's scratch root is known.

    Attributes:
        allow: Every entry of the role's `allow` list that names no placeholder, parsed.
        scratch_templates: Every entry that names `{scratch}`, validated but kept as a string
            until `role_set` fills it with a lease's POSIX scratch root.
        proposed: What an approval grants when the operator names no capabilities; set for the
            `device` role only, None for every other role.
    """

    allow: CapabilitySet
    scratch_templates: tuple[str, ...]
    proposed: CapabilitySet | None


@dataclass(frozen=True, slots=True)
class GuardPolicy:
    """The Guard's checked configuration: role defaults, the deny list and escalation per point.

    Attributes:
        roles: Every policy role's defaults, keyed by role name (exactly `POLICY_ROLES`).
        deny: Capabilities no principal may exercise, whatever it holds; checked at every
            enforcement point before the held set, and removed from every Warden's set.
        escalation: What a denial at a point escalates to; a point absent here refuses.
    """

    roles: Mapping[str, RoleDefaults]
    deny: CapabilitySet
    escalation: Mapping[EnforcementPoint, EscalationAction]

    def escalation_for(self, point: EnforcementPoint) -> EscalationAction:
        """Return what a denial at `point` escalates to.

        Args:
            point: The enforcement point that refused.

        Returns:
            The configured action, or `EscalationAction.REFUSE` when the point has none: a bare
            refusal is always safe, so it is the default everywhere.
        """
        return self.escalation.get(point, EscalationAction.REFUSE)
