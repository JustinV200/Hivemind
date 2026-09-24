"""Define the ``[guard]`` section: the operator's overrides of the Guard policy.

The Guard (``hivemind.guard``, the Hive's policy engine) starts from a policy shipped inside the
package: each role's default capability set, a hive-wide deny list and an escalation table
(ADR-0031). ``[guard]`` is how an operator changes it without touching code: ``policy_file``
replaces the shipped policy with a file of the same shape, ``[guard.roles.<role>]`` replaces one
role's ``allow`` list (and, for the ``device`` role, its ``proposed`` list), ``deny`` adds
capabilities no principal may exercise, and ``[guard.escalation]`` maps an enforcement point to
what a denial there leads to. This module checks only the shape of what it holds: every
capability entry is a non-empty string with no whitespace. Whether an entry parses, and whether a
role, point or action name exists, is decided by ``hivemind.guard`` when it builds the policy
(``load_guard_policy``), because the manifest sits a layer below ``guard`` and may not import it;
that build runs in the composition root, so a bad table still stops the Hive at start and the
error names the offending entry.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``; read by ``hivemind.cli.compose.deps``,
    which resolves ``policy_file`` against the manifest and hands both to
    ``hivemind.guard.policy.load_guard_policy``. The shipped policy file has this same shape,
    less ``policy_file``, and is validated with these same models. Calls into pydantic only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - An empty ``policy_file`` means "the policy shipped in ``hivemind.guard.defaults``"; a set
      one is resolved relative to the manifest's own directory (``HiveManifest.resolve_path``),
      never read here.
    - Nothing here can widen anything by itself: a role override is still checked against every
      enforcement point's rules, and ``deny`` only ever removes.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the policy model.
    - hivemind.guard.policy.defaults for load_guard_policy, which validates the names and entries.
    - hivemind.guard.defaults for the shipped policy.toml these fields overlay.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GuardRoleSection", "GuardSection"]

# One capability string as the manifest holds it: non-empty with no whitespace. The grammar
# itself is hivemind.guard's to check, one layer up (codingrules section 4).
_CapabilitySpec = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class GuardRoleSection(BaseModel):
    """One ``[guard.roles.<role>]`` table: a role's default capability set, replacing the policy's.

    The role name is the table's key; ``hivemind.guard`` refuses a name that is not a policy role.
    """

    model_config = _MODEL_CONFIG

    allow: tuple[_CapabilitySpec, ...] = Field(
        description="The role's whole default set, replacing the policy's list for this role; "
        "may name the {scratch} placeholder, filled with a lease's scratch root."
    )
    proposed: tuple[_CapabilitySpec, ...] | None = Field(
        default=None,
        description="For the device role only: what an approval grants when the operator names "
        "no capabilities; never wider than allow. None keeps the policy's own list; any other "
        "role giving one is refused when the policy is built.",
    )


class GuardSection(BaseModel):
    """``[guard]``: a replacement policy file, per-role overrides, denials and escalation."""

    model_config = _MODEL_CONFIG

    policy_file: str = Field(
        default="",
        description="A Guard policy TOML file replacing the one shipped in "
        "hivemind.guard.defaults, resolved relative to the manifest's own directory; empty "
        "means the shipped policy.",
    )
    deny: tuple[_CapabilitySpec, ...] = Field(
        default=(),
        description="Capabilities no principal may exercise, whatever it holds; added to the "
        "policy's own deny list, never replacing it.",
    )
    roles: dict[str, GuardRoleSection] = Field(
        default_factory=dict,
        description="[guard.roles.<role>] overrides keyed by policy role name (queen, operator, "
        "warden, device, swarm_device, or a lowercase Worker role); a role absent here keeps the "
        "policy's own set.",
    )
    escalation: dict[str, str] = Field(
        default_factory=dict,
        description="[guard.escalation]: enforcement point name to escalation action name "
        "('refuse', 'alarm' or 'ask_human'); a point absent here keeps the policy's own action, "
        "and one the policy does not name refuses.",
    )
