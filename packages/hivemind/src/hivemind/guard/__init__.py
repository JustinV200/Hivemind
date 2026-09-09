"""Define the Hive's policy engine: CapabilitySet, and the access rules for AccessLevel.

`CapabilitySet` is what one bee is currently allowed to do, built from `Capability` grants across
the families in `hivemind.guard.capabilities` (tool, filesystem read/write, network, exec, device,
spend). `hivemind.guard.access` says what each `AccessLevel` (a Real Cell's READ_ONLY/SCRATCH/FULL
tier) permits, as data: `ceiling_for` builds the widest set a level ever allows, and
`cap_to_access` narrows a requested set down to what that ceiling allows. The security tier enums
themselves (`AccessLevel`, `CombShieldLevel`, `HoneyClearance`) live in `hivemind.cell.tiers`;
guard only interprets `AccessLevel` here. Phase 3 step 3.13a builds this pure core -- the families
and rules 3.15 (worker capabilities) and 3.16 (the Drone's tools) already gate on -- and phase 10
step 10.1 extends the family list and adds the policy engine proper (`[guard]` manifest section,
enforcement points) once the Entrance, Honey and Exoskeleton subsystems exist.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by every layer above it, before
    an action is allowed to proceed. Calls into the security enums `hivemind.cell` defines, and
    `hivemind.common`.

Key invariants:
    - `CapabilitySet.attenuate` and `hivemind.guard.access.cap_to_access` never widen: both only
      ever return a subset of an existing set (codingrules section 15).
    - This package is pure: no I/O, per this step's brief.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/codingrules.md section 15 for the least-privilege rules this package encodes.
    - .claude/roadmap.md phase 3 step 3.13a for this step's scope, and phase 10 step 10.1 for the
      policy engine and remaining Capability families this package leaves for later.

Public API:
    - CapabilityFamily, Capability, CapabilitySet: the capability model (capabilities).
    - ceiling_for, cap_to_access: what each AccessLevel permits, as data (access).
    - GuardError, InvalidCapabilityError, CapabilityWideningError: this package's error tree
      (errors).
"""

from hivemind.guard.access import cap_to_access, ceiling_for
from hivemind.guard.capabilities import Capability, CapabilityFamily, CapabilitySet
from hivemind.guard.errors import CapabilityWideningError, GuardError, InvalidCapabilityError

__all__ = [
    "Capability",
    "CapabilityFamily",
    "CapabilitySet",
    "CapabilityWideningError",
    "GuardError",
    "InvalidCapabilityError",
    "cap_to_access",
    "ceiling_for",
]
