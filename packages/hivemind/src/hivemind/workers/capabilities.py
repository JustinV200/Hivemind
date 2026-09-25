"""Define worker_capabilities: a Worker's CapabilitySet slice, never wider than its Warden's.

`worker_capabilities` is the one function that turns a Warden's own `CapabilitySet` plus a task's
`TaskNeeds` into the strict, narrower slice one Worker receives (codingrules section 15:
"Capabilities and Forage only attenuate down the tree"). Every Worker always gets `fs:write`
confined to its own scratch directory, whatever else it asks for; a network scope
(`TaskNeeds.network_scopes`) or an Exoskeleton attachment (`TaskNeeds.exoskeleton`) is granted
only when the task actually needs it *and* the Warden itself already holds that capability --
never invented, never wider. Roadmap step 5.0e: `extra_write_roots` adds one more `fs:write`
candidate per root (the manifest's own `keep_root` and each of the task's declared `leaves`
roots), filtered through the exact same `warden_caps.allows(...)` gate as everything else -- this
is what actually lets the `keep` tool's own COPY proposal (and a declared outside-scratch DIFF)
pass `PathAllowlistCheck`'s capability check at all: without it, a Worker's own `fs:write` never
reaches past scratch regardless of the Cell's `AccessLevel`, since `TaskNeeds` itself carries no
field for "this task may write outside scratch." The final call is `CapabilitySet.attenuate`,
which either returns the computed slice unchanged or raises `CapabilityWideningError`; calling it
here is deliberately defensive (the slice is already filtered down to what the Warden allows
before that call), so a raise from it can only mean this function's own filtering has a bug,
proving the invariant rather than relying on it. Roadmap step 7.8: the baseline names
`tool:recall` and `tool:remember` explicitly, the two Honey Store tools every Drone is granted
(`hivemind.workers.tools.registry.build_registry` offers each only when it is allowed), so they
survive a Warden ceiling that one day lists tools by name instead of `tool:*`.

Fits into the Hive:
    Layer 4 (roles that do the work). Called by `hivemind.wardens.spawn` (roadmap step 3.19) when
    it spawns a sub-bee, to attenuate its own `CapabilitySet` down to the new Worker's. Calls into
    `hivemind.cell` (TaskNeeds), `hivemind.guard` (Capability, CapabilityFamily, CapabilitySet)
    only.

Key invariants:
    - The result is never wider than `warden_caps`: every candidate capability is checked with
      `warden_caps.allows(...)` before it is even offered to the final `attenuate` call --
      including every `extra_write_roots` entry, so a Cell below `AccessLevel.FULL` (whose own
      ceiling never carries an unconfined `fs:write`) grants none of them, whatever a task
      declares.
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
    needs: TaskNeeds,
    scratch_root: Path,
    extra_write_roots: tuple[Path, ...] = (),
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
        extra_write_roots: Roadmap step 5.0e: one more `fs:write` candidate per root (the
            manifest's own `keep_root`, plus each of the task's declared `leaves` roots), still
            filtered through `warden_caps.allows(...)` like every other candidate here; empty by
            default so every pre-5.0e caller keeps building the same slice unchanged.

    Returns:
        A CapabilitySet that is always a subset of `warden_caps`.

    Raises:
        hivemind.guard.CapabilityWideningError: The computed slice would exceed `warden_caps`.
            Defensive only: every candidate is already filtered by `warden_caps.allows(...)`
            before this is possible, so reaching it means this function's own filtering broke.
    """
    wanted = (
        _baseline_specs(scratch_root) + _needs_specs(needs) + _write_root_specs(extra_write_roots)
    )
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
        to work at all, the two Honey Store tools by name among them (still capped by the
        Warden's own ceiling in `worker_capabilities`).
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
        "tool:recall",  # Roadmap 7.8: ask the Honey Store (the Warden's ceiling still decides).
        "tool:remember",  # Roadmap 7.8: deposit a finding into it; no side effect on the Cell.
    ]


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
        `<posix-path>/**` shape `_baseline_specs`/`hivemind.guard.access.ceiling_for` use, for a
        root that is a directory a task writes files under).
    """
    specs: list[str] = []
    for root in roots:
        posix_root = root.as_posix().rstrip("/")
        specs.append(f"fs:write:{posix_root}")
        specs.append(f"fs:write:{posix_root}/**")
    return specs


def _needs_specs(needs: TaskNeeds) -> list[str]:
    """Return the capability strings `needs` asks for beyond the baseline.

    Args:
        needs: The task's TaskNeeds.

    Returns:
        One `net:<scope>` entry per `needs.network_scopes`, plus the Exoskeleton peripherals
        `needs` asks for (roadmap phase 6, ADR-0031): the browser for any Exoskeleton need; a
        display, and the operator's running display where the Warden itself was granted it, for
        a desktop need; audio for an audio need. Empty when the task asks for none of these.
    """
    specs = [f"net:{scope}" for scope in needs.network_scopes]
    if not needs.exoskeleton:
        return specs
    # A desktop need includes the browser: a page on the desktop is still best driven through
    # the fast path's accessibility tree.
    specs.append("exoskeleton:browser")
    if not needs.browser_only:
        # real_display is a candidate like any other: worker_capabilities keeps it only where the
        # Warden holds it, which is only where the Cell's operator allowed it.
        specs.extend(["exoskeleton:display", "exoskeleton:real_display"])
    if needs.audio:
        specs.append("exoskeleton:audio")
    return specs
