"""Build a principal's capability set from its policy role: a role default, a Warden's set.

A `GuardPolicy` holds each policy role's default set with `{scratch}` entries still unfilled,
because only a lease knows its scratch root. `role_set` fills them and returns the role's set;
`warden_set` is a Warden's whole set (ADR-0039): its role default, narrowed to its Cell's access
level (`hivemind.guard.access.cap_to_access`) and stripped of every entry the hive-wide deny list
covers; `worker_role_name` names the policy role a wire `WorkerRole` starts from, and
`proposed_set` is what a device approval grants by default. Everything here is pure: the scratch
root is only written into scope strings.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.wardens.warden`
    (a Warden's set, at start) and `hivemind.wardens.spawn` (a Worker's role default, per spawn),
    and by the Entrance for a device's approval (roadmap step 10.5d). Calls into
    `hivemind.guard.access`, `hivemind.guard.capabilities` and this package's `table`.

Key invariants:
    - Nothing here widens a set: `warden_set` only ever removes entries from the role default,
      and `role_set` returns exactly the role's configured entries.
    - A Warden's set never holds an entry the deny list covers, nor a Cell-effect capability its
      Cell's access level does not permit.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Two roots, and sets
      only narrow below them".
    - hivemind.workers.capabilities for worker_capabilities, which narrows a role default to one
      Worker's slice of its Warden's set.
"""

from __future__ import annotations

from pathlib import PurePath

from hivemind.cell import AccessLevel
from hivemind.guard.access import cap_to_access, fill_scratch
from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.errors import GuardPolicyError
from hivemind.guard.policy.table import DEVICE_ROLE, WARDEN_ROLE, GuardPolicy, RoleDefaults
from waggle.messages.task import WorkerRole

__all__ = ["proposed_set", "role_set", "warden_set", "worker_role_name"]


def role_set(policy: GuardPolicy, role: str, scratch_root: PurePath | None = None) -> CapabilitySet:
    """Return a policy role's default set, with `{scratch}` filled from a lease's scratch root.

    Args:
        policy: The Guard policy to read the role from.
        role: A policy role name (`"warden"`, `"drone"`, `"device"`, ...).
        scratch_root: The lease's scratch directory; needed only when the role names
            `{scratch}` (every Warden and Worker role does). None for a principal with no lease.

    Returns:
        Every entry of the role's `allow` list, placeholders filled.

    Raises:
        GuardPolicyError: `role` is not a role the policy defines, or it names `{scratch}` and
            `scratch_root` is None.
    """
    defaults = _defaults(policy, role)
    if not defaults.scratch_templates:
        return defaults.allow  # Nothing to fill: the role names no scratch directory.
    if scratch_root is None:
        raise GuardPolicyError(
            f"Role {role!r} names a scratch directory, but no scratch root was given to fill it."
        )
    # Each template was checked when the policy loaded, so filling it in always parses.
    filled = frozenset(
        Capability.parse(fill_scratch(template, scratch_root))
        for template in defaults.scratch_templates
    )
    return CapabilitySet(capabilities=defaults.allow.capabilities | filled)


def warden_set(
    policy: GuardPolicy,
    access_level: AccessLevel,
    scratch_root: PurePath,
    *,
    real_display: bool = False,
) -> CapabilitySet:
    """Return a Warden's whole set: its role default, narrowed to its Cell, less the deny list.

    Args:
        policy: The Guard policy the Warden was built with.
        access_level: Its lease's access level; caps every Cell-effect family.
        scratch_root: Its lease's scratch directory, for `{scratch}` and SCRATCH's write scope.
        real_display: Whether its Cell's operator allowed the Hive to drive the display already
            running there (`CellCapabilities.real_display_allowed`, ADR-0031); without it no
            Warden holds `exoskeleton:real_display`, whatever its role default names.

    Returns:
        The `warden` role's set, kept only where `access_level` permits a Cell effect, with every
        entry the policy's deny list covers removed. Always a subset of the role default.
    """
    narrowed = cap_to_access(
        role_set(policy, WARDEN_ROLE, scratch_root),
        access_level,
        scratch_root,
        real_display=real_display,
    )
    # An entry the deny list covers could only ever be refused at its point; dropping it here
    # keeps it out of everything the Warden hands down, too.
    kept = frozenset(capability for capability in narrowed if not policy.deny.allows(capability))
    return CapabilitySet(capabilities=kept)


def proposed_set(policy: GuardPolicy) -> CapabilitySet:
    """Return what a device approval grants when the operator names no capabilities.

    Args:
        policy: The Guard policy to read the `device` role from.

    Returns:
        The `device` role's `proposed` set: inside its `allow` ceiling, without stewardship or
        Night Veil, which are only ever granted by name.
    """
    proposed = _defaults(policy, DEVICE_ROLE).proposed
    # The loader refuses a policy whose device role has no proposed list, so this always holds.
    return proposed if proposed is not None else CapabilitySet.empty()


def worker_role_name(role: WorkerRole) -> str:
    """Return the policy role a Worker of `role` starts from: the lowercase wire name.

    Args:
        role: The `TaskAssign.role` a Warden is spawning.

    Returns:
        `"drone"` for `WorkerRole.DRONE`, `"guard_bee"` for `WorkerRole.GUARD_BEE`, and so on:
        the same lowercase keys `[placement.roles]` and `[forage.roles]` use.
    """
    return role.name.lower()


def _defaults(policy: GuardPolicy, role: str) -> RoleDefaults:
    """Return one role's defaults, or raise naming the roles the policy has."""
    defaults = policy.roles.get(role)
    if defaults is None:
        raise GuardPolicyError(
            f"The Guard policy defines no role {role!r}; its roles are {sorted(policy.roles)}."
        )
    return defaults
