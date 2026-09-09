"""Define worker_capabilities: a Worker's CapabilitySet slice, never wider than its Warden's.

`worker_capabilities` is the one function that turns a Warden's own `CapabilitySet` plus a task's
`TaskNeeds` into the strict, narrower slice one Worker receives (codingrules section 15:
"Capabilities and Forage only attenuate down the tree"). Every Worker always gets `fs:write`
confined to its own scratch directory, whatever else it asks for; a network scope
(`TaskNeeds.network_scopes`) or an Exoskeleton attachment (`TaskNeeds.exoskeleton`) is granted
only when the task actually needs it *and* the Warden itself already holds that capability --
never invented, never wider. The final call is `CapabilitySet.attenuate`, which either returns the
computed slice unchanged or raises `CapabilityWideningError`; calling it here is deliberately
defensive (the slice is already filtered down to what the Warden allows before that call), so a
raise from it can only mean this function's own filtering has a bug, proving the invariant rather
than relying on it.

Fits into the Hive:
    Layer 4 (roles that do the work). Called by `hivemind.wardens.spawn` (roadmap step 3.19) when
    it spawns a sub-bee, to attenuate its own `CapabilitySet` down to the new Worker's. Calls into
    `hivemind.cell` (TaskNeeds), `hivemind.guard` (Capability, CapabilityFamily, CapabilitySet)
    only.

Key invariants:
    - The result is never wider than `warden_caps`: every candidate capability is checked with
      `warden_caps.allows(...)` before it is even offered to the final `attenuate` call.
    - `fs:write` under the Worker's own `scratch_root` is always present, regardless of `needs`.
    - A `net`/`device` capability is present only when `needs` asks for it (`network_scopes`,
      `exoskeleton`) AND `warden_caps` already grants it.

See Also:
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the
      tree", the invariant this module's one function upholds and proves.
    - hivemind.guard.capabilities for Capability, CapabilityFamily and CapabilitySet.attenuate.
    - hivemind.guard.access for ceiling_for/cap_to_access, the equivalent narrowing a Real Cell's
      AccessLevel performs; this module narrows a second time, per Worker rather than per Cell.
    - hivemind.cell.needs for TaskNeeds, the source of the extra families this module may add.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import TaskNeeds
from hivemind.guard import CapabilitySet

__all__ = ["worker_capabilities"]


def worker_capabilities(
    warden_caps: CapabilitySet, needs: TaskNeeds, scratch_root: Path
) -> CapabilitySet:
    """Compute one Worker's strict CapabilitySet slice from its Warden's set and the task's needs.

    Args:
        warden_caps: The spawning Warden's own CapabilitySet; the ceiling this function's result
            may never exceed.
        needs: The task's TaskNeeds; `network_scopes` and `exoskeleton` are the only fields that
            add a family beyond the baseline (`fs:write` under scratch, `fs:read` and `exec`/
            `tool` for whatever a role's tools need to run).
        scratch_root: This Worker's own scratch directory; every relative path a tool touches
            resolves against it.

    Returns:
        A CapabilitySet that is always a subset of `warden_caps`.

    Raises:
        hivemind.guard.CapabilityWideningError: The computed slice would exceed `warden_caps`.
            Defensive only: every candidate is already filtered by `warden_caps.allows(...)`
            before this is possible, so reaching it means this function's own filtering broke.
    """
    wanted = _baseline_specs(scratch_root) + _needs_specs(needs)
    candidate = CapabilitySet.parse(*wanted)
    # Keep only what the Warden itself already grants: this is where "extra families only when
    # needs asks for them AND the warden has them" actually happens, uniformly for the baseline
    # and the needs-derived entries alike.
    granted = CapabilitySet(
        capabilities=frozenset(
            capability for capability in candidate if warden_caps.allows(capability)
        )
    )
    # Proves the slice invariant rather than relying on the filtering above alone (module
    # docstring): raises only if this function's own filtering let something wider through.
    return warden_caps.attenuate(granted)


def _baseline_specs(scratch_root: Path) -> list[str]:
    """Return the capability strings every Worker gets, whatever its task asks for.

    Args:
        scratch_root: This Worker's own scratch directory.

    Returns:
        `fs:write` confined to `scratch_root`, plus the read/exec/tool access a role's tools need
        to work at all (still capped by the Warden's own ceiling in `worker_capabilities`).
    """
    # Mirrors the pattern hivemind.guard.access.ceiling_for uses for a lease's own fs:write scope:
    # a posix-style path with a trailing "/**", stripped of a doubled slash for a root ending
    # in "/".
    posix_root = scratch_root.as_posix().rstrip("/")
    return [
        f"fs:write:{posix_root}/**",
        "fs:read:**",  # Every Cell tier already permits reads anywhere (codingrules 8.7).
        "exec:*",  # To run commands through the Cell's session.
        "tool:*",  # To call whatever a role's own tool registry (roadmap 3.16) offers it.
    ]


def _needs_specs(needs: TaskNeeds) -> list[str]:
    """Return the capability strings `needs` asks for beyond the baseline.

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
