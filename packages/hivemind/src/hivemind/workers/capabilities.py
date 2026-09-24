"""Define worker_capabilities: a Worker's CapabilitySet slice, never wider than its Warden's.

`worker_capabilities` is the one function that turns a Warden's own `CapabilitySet`, the Worker
role's default set and a task's `TaskNeeds` into the strict, narrower slice one Worker receives
(codingrules section 15: "Capabilities and Forage only attenuate down the tree"). The role default
comes from the Guard policy (`hivemind.guard.policy.role_set`, ADR-0031; a Drone's is its scratch
writes, reads anywhere, commands, every tool, its model slot and the rest of its `[guard.roles]`
table), built by the caller with the lease's scratch root filled in. On top of it, a network scope
(`TaskNeeds.network_scopes`) or an Exoskeleton attachment (`TaskNeeds.exoskeleton`) is added only
when the task actually needs it, and roadmap step 5.0e's `extra_write_roots` adds one more
`fs:write` candidate per root (the manifest's own `keep_root` and each of the task's declared
`leaves` roots): without them, a Worker's own `fs:write` never reaches past scratch regardless of
the Cell's `AccessLevel`, since `TaskNeeds` itself carries no field for "this task may write
outside scratch". Every candidate, the role default's included, is kept only when the Warden
itself already holds it -- never invented, never wider. The final call is
`CapabilitySet.attenuate`, which either returns the computed slice unchanged or raises
`CapabilityWideningError`; calling it here is deliberately defensive (the slice is already
filtered down to what the Warden allows before that call), so a raise from it can only mean this
function's own filtering has a bug, proving the invariant rather than relying on it.

Fits into the Hive:
    Layer 4 (roles that do the work). Called by `hivemind.wardens.spawn` (roadmap step 3.19) when
    it spawns a sub-bee, to attenuate its own `CapabilitySet` down to the new Worker's. Calls into
    `hivemind.cell` (TaskNeeds) and `hivemind.guard` (CapabilitySet) only.

Key invariants:
    - The result is never wider than `warden_caps`: every candidate capability is checked with
      `warden_caps.allows(...)` before it is even offered to the final `attenuate` call --
      including every role-default entry and every `extra_write_roots` entry, so a Cell below
      `AccessLevel.FULL` (whose Warden never holds an unconfined `fs:write`) grants none of the
      latter, whatever a task declares.
    - Every role-default entry the Warden holds is present, regardless of `needs`.
    - A `net`/`device` capability beyond the role default is present only when `needs` asks for
      it (`network_scopes`, `exoskeleton`) AND `warden_caps` already grants it.

See Also:
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the
      tree", the invariant this module's one function upholds and proves.
    - hivemind.guard.policy.roles for role_set, which builds `role_default`, and warden_set, which
      builds the Warden's own set the same way one level up.
    - hivemind.cell.needs for TaskNeeds, the source of the extra families this module may add.
    - hivemind.wardens.spawn.spawn for _widen_lease_reachability, the sibling fix (roadmap step
      5.0e) that does the same "declared leaves root, keep_root" widening for lease/session
      reachability rather than capabilities -- both are needed for an outside-scratch write to
      actually land.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import TaskNeeds
from hivemind.guard import CapabilitySet

__all__ = ["worker_capabilities"]


def worker_capabilities(
    warden_caps: CapabilitySet,
    role_default: CapabilitySet,
    needs: TaskNeeds,
    extra_write_roots: tuple[Path, ...] = (),
) -> CapabilitySet:
    """Compute one Worker's strict CapabilitySet slice from its Warden's set and its role's.

    Args:
        warden_caps: The spawning Warden's own CapabilitySet; the ceiling this function's result
            may never exceed.
        role_default: The Worker role's default set from the Guard policy
            (`hivemind.guard.policy.role_set(policy, role, scratch_root)`), `{scratch}` already
            filled with this Worker's scratch root.
        needs: The task's TaskNeeds; `network_scopes` and `exoskeleton` are the only fields that
            add a family beyond the role default.
        extra_write_roots: Roadmap step 5.0e: one more `fs:write` candidate per root (the
            manifest's own `keep_root`, plus each of the task's declared `leaves` roots), still
            filtered through `warden_caps.allows(...)` like every other candidate here; empty by
            default so a caller with none builds the plain role-and-needs slice.

    Returns:
        A CapabilitySet that is always a subset of `warden_caps`.

    Raises:
        hivemind.guard.InvalidCapabilityError: A `needs.network_scopes` entry is not a valid
            `net` scope (a host, `*.domain`, an IP literal or a CIDR network).
        hivemind.guard.CapabilityWideningError: The computed slice would exceed `warden_caps`.
            Defensive only: every candidate is already filtered by `warden_caps.allows(...)`
            before this is possible, so reaching it means this function's own filtering broke.
    """
    extra = CapabilitySet.parse(*_needs_specs(needs), *_write_root_specs(extra_write_roots))
    # Keep only what the Warden itself already grants: this is where "extra families only when
    # needs asks for them AND the warden has them" actually happens, uniformly for the role
    # default and the needs-derived entries alike.
    granted = CapabilitySet(
        capabilities=frozenset(
            capability for capability in (*role_default, *extra) if warden_caps.allows(capability)
        )
    )
    # Proves the slice invariant rather than relying on the filtering above alone (module
    # docstring): raises only if this function's own filtering let something wider through.
    return warden_caps.attenuate(granted)


def _write_root_specs(roots: tuple[Path, ...]) -> list[str]:
    """Return two `fs:write` candidates per root (roadmap step 5.0e): the root itself, and nested.

    Args:
        roots: Already-resolved directories (or files) outside scratch a Worker may need to write
            under -- `keep_root` and each declared leaving's own conservative root
            (`hivemind.supervision.capping.leave.declared_leaving_root`).

    Returns:
        Two capability strings per root: `fs:write:<root>` (an exact match, for a leaving that is
        itself the one file to write -- `Capability.matches`' own `fnmatch` never matches a bare
        path against a `/**`-suffixed pattern) and `fs:write:<root>/**` (the same
        `<posix-path>/**` shape `hivemind.guard.access.fill_scratch` gives a scratch root, for a
        root that is a directory a task writes files under).
    """
    specs: list[str] = []
    for root in roots:
        posix_root = root.as_posix().rstrip("/")
        specs.append(f"fs:write:{posix_root}")
        specs.append(f"fs:write:{posix_root}/**")
    return specs


def _needs_specs(needs: TaskNeeds) -> list[str]:
    """Return the capability strings `needs` asks for beyond the role default.

    Args:
        needs: The task's TaskNeeds.

    Returns:
        One `net:<scope>` entry per `needs.network_scopes`, plus `device:*` when
        `needs.exoskeleton` is set; empty when the task asks for neither.
    """
    specs = [f"net:{scope}" for scope in needs.network_scopes]
    if needs.exoskeleton:
        specs.append("device:*")
    return specs
