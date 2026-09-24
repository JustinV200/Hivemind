"""Define what each AccessLevel permits on a Cell, as data, and narrow a set to it.

`AccessLevel` (`hivemind.cell.tiers`: `READ_ONLY`, `SCRATCH`, `FULL`) is set when a Real Cell (an
existing device the Hive borrows and leaves exactly as found) joins, is stored with the node and
every lease, and caps every capability set issued for that Cell; a Virtual Cell is always `FULL`.
ADR-0031 fixes what it caps: only the families that act on the machine itself
(`CELL_EFFECT_FAMILIES`: filesystem reads and writes, exec, network, devices, paths outside
scratch, the Exoskeleton, location and host metadata). Every other family (a tool, a model slot, a
question to the human, a spend ceiling, a tactic, watch) passes through untouched, so a
`READ_ONLY` Warden still holds `question:human` and `llm:warden` and can never hold a write, an
exec or a network scope. `ceiling_for` builds the widest governed set a level permits,
`cap_to_access` narrows a requested set to it, and `admits` says whether a level permits a family
at all, which is what the policy engine's access-level rule reads.

This step changes phase 3's `ceiling_for` (roadmap 3.13a): `tool` and `spend` are no longer in
any ceiling, because neither is an effect on the Cell; a tool's effects are checked through the
families it exercises (its `fs:write`, `exec` or `net`), and spend is bounded by grants.

Watch mode (a Real Cell's Warden observing with no active bees, phase 11.10) is bounded by
`READ_ONLY` on that device, whatever `watch:<node>` capability names it (roadmap 10.7): it may
observe the process list, resource use, logs in allowed roots and file-change events in allowed
roots, never writes, and never captures the screen or input, which is a separate capability that
is never issued implicitly. `watch` itself is not governed here, because it grants no effect on
the Cell; what a watcher may touch is exactly the `READ_ONLY` ceiling above.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.guard.policy`
    (a Warden's set, and the access-level rule of `evaluate`) and by any layer that issues a set
    for a Cell. Calls into `hivemind.guard.capabilities` and `hivemind.cell.tiers` only.

Key invariants:
    - `cap_to_access(requested, level, root)` is always a subset of `requested`, and its governed
      part is always a subset of `ceiling_for(level, root)`; it narrows and never widens
      (hypothesis property tests in `tests/unit/guard/test_access.py`).
    - A `READ_ONLY` result holds no governed family but `fs:read`: no write, exec, network,
      device, outside-scratch, Exoskeleton, location or host-metadata capability, however
      `requested` was built.
    - The levels nest: each ceiling holds everything the level below it holds.
    - Pure: `scratch_root` is only written into a scope string, never read from disk.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Access levels narrow
      only what touches the Cell".
    - .claude/codingrules.md section 8.7 for what each AccessLevel means for a Real Cell.
    - .claude/roadmap.md step 10.7 for the access-level data and watch mode's bound.
    - hivemind.cell.tiers for AccessLevel itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePath, PurePosixPath
from types import MappingProxyType

from hivemind.cell.tiers import AccessLevel
from hivemind.guard.capabilities import CapabilityFamily, CapabilitySet

SCRATCH_PLACEHOLDER = "{scratch}"  # Stands for a lease's POSIX scratch root inside a capability.
_SAMPLE_SCRATCH_ROOT = PurePosixPath("/scratch")  # Any root will do where only families matter.

# The families that act on the machine itself (ADR-0031): the only ones an AccessLevel caps.
CELL_EFFECT_FAMILIES: frozenset[CapabilityFamily] = frozenset(
    {
        CapabilityFamily.FS_READ,
        CapabilityFamily.FS_WRITE,
        CapabilityFamily.EXEC,
        CapabilityFamily.NET,
        CapabilityFamily.DEVICE,
        CapabilityFamily.CELL_OUTSIDE_SCRATCH,
        CapabilityFamily.EXOSKELETON,
        CapabilityFamily.EXOSKELETON_REAL_DISPLAY,
        CapabilityFamily.GEO,
        CapabilityFamily.WIFI_SCAN,
        CapabilityFamily.HOST_METADATA,
    }
)

# What each level adds to the one below it (READ_ONLY < SCRATCH < FULL, AccessLevel.rank).
_LEVEL_GRANTS: Mapping[AccessLevel, tuple[str, ...]] = MappingProxyType(
    {
        # Reading is harmless to the machine, so even READ_ONLY reads anywhere.
        AccessLevel.READ_ONLY: ("fs:read:**",),
        # Writes stay inside the lease's own scratch directory; commands may run, since Capping
        # still gates any effect they have outside scratch before it lands.
        AccessLevel.SCRATCH: (f"fs:write:{SCRATCH_PLACEHOLDER}/**", "exec:*"),
        # The whole Cell, within its other controls: writes anywhere, the network, devices,
        # paths outside scratch, the Exoskeleton (on the Cell's own display too), and the
        # location and host facts only a full grant should ever reveal.
        AccessLevel.FULL: (
            "fs:write:**",
            "net:*",
            "device:*",
            "cell:outside_scratch:**",
            "exoskeleton",
            "exoskeleton:real_display",
            "geo:*",
            "wifi:scan",
            "host:metadata",
        ),
    }
)

__all__ = [
    "CELL_EFFECT_FAMILIES",
    "SCRATCH_PLACEHOLDER",
    "admits",
    "cap_to_access",
    "ceiling_for",
    "fill_scratch",
    "governs",
]


def governs(family: CapabilityFamily) -> bool:
    """Decide whether an AccessLevel caps `family` at all.

    Args:
        family: Any capability family.

    Returns:
        True for a family in `CELL_EFFECT_FAMILIES`; False for every other family, which access
        levels pass through untouched.
    """
    return family in CELL_EFFECT_FAMILIES


def ceiling_for(level: AccessLevel, scratch_root: PurePath) -> CapabilitySet:
    """Build the widest set of Cell-effect capabilities an AccessLevel permits on one Cell.

    Args:
        level: The Cell's access level (a Virtual Cell is always FULL).
        scratch_root: The lease's scratch directory; written into SCRATCH's `fs:write` scope
            (and so FULL's, which includes it) in POSIX form.

    Returns:
        Every governed capability `level` permits, each level including the ones below it. No
        family outside `CELL_EFFECT_FAMILIES` ever appears here.
    """
    # Levels nest by rank: gather every level's grants up to and including this one.
    specs = [
        fill_scratch(spec, scratch_root)
        for granted_level, grants in _LEVEL_GRANTS.items()
        if granted_level.rank <= level.rank
        for spec in grants
    ]
    return CapabilitySet.parse(*specs)


def admits(level: AccessLevel, family: CapabilityFamily) -> bool:
    """Decide whether an AccessLevel permits any capability of `family`, whatever its scope.

    The policy engine's access-level rule reads this: it knows a Cell's level but not the lease's
    scratch root, so it refuses a family the level never permits (an `exec` on a READ_ONLY Cell)
    and leaves the exact scope (a SCRATCH write inside or outside scratch) to the held set, which
    was narrowed with the real root when it was built.

    Args:
        level: The Cell's access level.
        family: The family of the capability an action needs.

    Returns:
        True for a family `level` does not govern, or one its ceiling holds at some scope.
    """
    return not governs(family) or family in _ADMITTED[level]


def cap_to_access(
    requested: CapabilitySet, level: AccessLevel, scratch_root: PurePath
) -> CapabilitySet:
    """Narrow a requested set to what an AccessLevel permits, leaving ungoverned families alone.

    Args:
        requested: The set someone would like to issue for a Cell at `level`.
        level: That Cell's access level.
        scratch_root: The lease's scratch directory, for the ceiling's `fs:write` scope.

    Returns:
        Every capability of `requested` whose family access levels do not govern, unchanged, plus
        every governed one the level's ceiling allows. Never anything `requested` lacks.
    """
    ceiling = ceiling_for(level, scratch_root)
    kept = frozenset(
        capability
        for capability in requested
        if not governs(capability.family) or ceiling.allows(capability)
    )
    return CapabilitySet(capabilities=kept)


def fill_scratch(template: str, scratch_root: PurePath) -> str:
    """Substitute a scratch root for every `{scratch}` placeholder in a capability string.

    Args:
        template: A capability string that may contain `SCRATCH_PLACEHOLDER`.
        scratch_root: The lease's scratch directory, in whatever path style the platform gives.

    Returns:
        `template` with each placeholder replaced by the root's POSIX form, stripped of a
        trailing "/" so `{scratch}/**` never becomes a doubled `//**` for a root of "/". Glob
        matching normalises backslashes on both sides, so a Windows root still compares correctly.
    """
    posix_root = scratch_root.as_posix().rstrip("/")
    return template.replace(SCRATCH_PLACEHOLDER, posix_root)


def _admitted_families() -> Mapping[AccessLevel, frozenset[CapabilityFamily]]:
    """Map each level to the governed families its ceiling holds at any scope."""
    return MappingProxyType(
        {
            level: frozenset(
                capability.family for capability in ceiling_for(level, _SAMPLE_SCRATCH_ROOT)
            )
            for level in AccessLevel
        }
    )


# Computed once at import from the table above (pure, codingrules 5.5), so admits() is a lookup.
_ADMITTED = _admitted_families()
