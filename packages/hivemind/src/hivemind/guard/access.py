"""Define what each AccessLevel permits, as data: ceiling_for and cap_to_access.

`AccessLevel` (`hivemind.cell.tiers`: `READ_ONLY`, `SCRATCH`, `FULL`) is set at enrolment for a
Real Cell (an existing device the Hive borrows and leaves exactly as found) and caps every
capability set issued for that Cell "however the policy is configured" (codingrules section 8.7).
This module is where that cap becomes code: `ceiling_for` builds the widest `CapabilitySet` a
level ever permits, and `cap_to_access` narrows a requested set down to what a level's ceiling
allows, so "a READ_ONLY device can never receive a write capability" is a property of this
function's output, not of whoever calls it correctly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by `hivemind.cell.local` and
    `hivemind.swarm` (Layer 3) when a lease is opened, and by `hivemind.workers.capabilities`
    (phase 3 step 3.15) when a Warden's set is attenuated down to one Worker's. Calls into
    `hivemind.guard.capabilities` and `hivemind.cell.tiers` (`AccessLevel`) only.

Key invariants:
    - `ceiling_for` never reads `scratch_root`'s contents; it only writes the path into a
      capability scope string. This module is pure (no I/O, per this step's brief).
    - `cap_to_access(requested, level, scratch_root).issubset(ceiling_for(level, scratch_root))`
      always holds, for any `requested` (a hypothesis property test in
      `tests/unit/guard/test_access.py` checks it): `cap_to_access` only ever keeps entries from
      `requested` that the ceiling already allows, so it can narrow but never widen.
    - `AccessLevel.FULL` is the only level whose ceiling includes `net`, `device` or `spend`
      capabilities at all; Virtual Cells are always `FULL` (codingrules section 6.1).

See Also:
    - .claude/codingrules.md section 8.7 for "Every Real Cell has an access level" and what each
      one permits.
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the
      tree", the property `cap_to_access` guarantees.
    - hivemind.cell.tiers for `AccessLevel` itself.
    - hivemind.guard.capabilities for `Capability` and `CapabilitySet`, the values this module
      builds and narrows.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell.tiers import AccessLevel
from hivemind.guard.capabilities import CapabilitySet

__all__ = ["cap_to_access", "ceiling_for"]


def ceiling_for(level: AccessLevel, scratch_root: Path) -> CapabilitySet:
    """Build the widest CapabilitySet an AccessLevel ever permits on one Cell.

    The three levels nest (READ_ONLY < SCRATCH < FULL, `AccessLevel.rank`): each ceiling below
    includes everything the previous one grants, plus what that level adds.

    Args:
        level: The Real Cell's access level (Virtual Cells are always FULL).
        scratch_root: The lease's scratch directory. Only used to build the SCRATCH/FULL
            `fs:write` scope; READ_ONLY never reads it.

    Returns:
        The CapabilitySet no capability set issued for a Cell at `level` may exceed.
    """
    # Every level, including READ_ONLY, may read anywhere: "READ_ONLY: fs:read:** only, no exec,
    # no write, no net" (this step's brief) still means reads are unrestricted.
    specs: list[str] = ["fs:read:**"]
    if level is AccessLevel.READ_ONLY:
        return CapabilitySet.parse(*specs)

    # SCRATCH: writes stay inside the lease's own scratch directory (codingrules section 8.7,
    # "Writes stay inside the lease's own scratch directory"); commands and tools may run, since
    # Capping (supervision.capping) still gates their effects before anything lands.
    specs.append(_scratch_write_scope(scratch_root))
    specs.append("exec:*")
    specs.append("tool:*")
    if level is AccessLevel.SCRATCH:
        return CapabilitySet.parse(*specs)

    # FULL: "the whole Cell is reachable, within the Cell's other controls" (codingrules 8.7),
    # so writes are no longer confined to scratch, and network, device and spend are unlocked.
    specs.extend(["fs:write:**", "net:*", "device:*", "spend:*"])
    return CapabilitySet.parse(*specs)


def cap_to_access(
    requested: CapabilitySet, level: AccessLevel, scratch_root: Path
) -> CapabilitySet:
    """Narrow a requested CapabilitySet down to what an AccessLevel's ceiling allows.

    Keeps only the entries of `requested` the ceiling already permits; it can shrink `requested`
    but never grow it, so a READ_ONLY device can never receive a write capability, however
    `requested` was built.

    Args:
        requested: The capability set someone would like to grant.
        level: The Cell's access level to check `requested` against.
        scratch_root: Passed through to `ceiling_for` to build its SCRATCH/FULL `fs:write` scope.

    Returns:
        The subset of `requested` the level's ceiling allows.
    """
    ceiling = ceiling_for(level, scratch_root)
    granted = frozenset(
        capability for capability in requested.capabilities if ceiling.allows(capability)
    )
    return CapabilitySet(capabilities=granted)


def _scratch_write_scope(scratch_root: Path) -> str:
    """Build the `fs:write` capability string confined to one lease's scratch directory.

    Args:
        scratch_root: The lease's scratch directory, in whatever path style the platform gives it.

    Returns:
        `"fs:write:<posix-path>/**"`, with `scratch_root` rendered forward-slash style (`Path.
        as_posix`) so it compares correctly against a Windows caller's backslash paths too
        (`Capability.matches` normalises both sides the same way).
    """
    posix_root = scratch_root.as_posix().rstrip(
        "/"
    )  # Avoid a doubled "//**" for a root ending in "/".
    return f"fs:write:{posix_root}/**"
