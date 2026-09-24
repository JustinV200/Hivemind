"""Tests for hivemind.guard.policy.table: GuardPolicy, RoleDefaults and the policy role names.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/table.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.table for the module under test.
"""

from __future__ import annotations

from types import MappingProxyType

from hivemind.guard.capabilities import CapabilitySet
from hivemind.guard.policy.models import EscalationAction
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.guard.policy.table import (
    DEVICE_ROLE,
    POLICY_ROLES,
    WARDEN_ROLE,
    GuardPolicy,
    RoleDefaults,
)
from waggle.messages.task import WorkerRole


def test_policy_roles_are_the_roots_the_warden_every_worker_role_and_both_devices() -> None:
    assert {
        "operator",
        "queen",
        "warden",
        "drone",
        "forager",
        "scout",
        "guard_bee",
        "undertaker",
        "house_bee",
        "device",
        "swarm_device",
    } == POLICY_ROLES
    assert {role.name.lower() for role in WorkerRole} <= POLICY_ROLES
    assert {WARDEN_ROLE, DEVICE_ROLE} <= POLICY_ROLES


def test_escalation_for_a_point_with_no_entry_refuses() -> None:
    policy = GuardPolicy(
        roles=MappingProxyType({}),
        deny=CapabilitySet.empty(),
        escalation=MappingProxyType({EnforcementPoint.TOOL_INVOCATION: EscalationAction.ALARM}),
    )

    assert policy.escalation_for(EnforcementPoint.TOOL_INVOCATION) is EscalationAction.ALARM
    assert policy.escalation_for(EnforcementPoint.PLACEMENT) is EscalationAction.REFUSE


def test_role_defaults_keeps_templates_apart_from_parsed_entries() -> None:
    defaults = RoleDefaults(
        allow=CapabilitySet.parse("fs:read:**"),
        scratch_templates=("fs:write:{scratch}/**",),
        proposed=None,
    )

    assert defaults.allow.as_strings() == ("fs:read:**",)
    assert defaults.scratch_templates == ("fs:write:{scratch}/**",)
