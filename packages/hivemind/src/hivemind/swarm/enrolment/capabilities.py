"""Compute the capability strings a Pollen Packet is granted for an access level.

The packet checks every request it receives against `EnrolAccept.granted_capabilities` before any
executor runs, and refuses what is not covered with `pollen.capability_denied`
(docs/waggle/gateway-contract.md section 10). Those strings are the Guard's own per-level grants
(`hivemind.guard.access`: what each access level permits on a Cell, as data) cut down to the five
families a gateway can enforce on the machine itself: `exec`, `fs:read`, `fs:write`,
`cell:outside_scratch` and `watch`. The others (the network, devices, the Exoskeleton's display,
input and audio, location and host metadata) are enforced by the Hive Stand and never reach the
packet, so a string in any of them would only be dead weight a packet must ignore. The literal
`{scratch}` in a scope stands for each lease's scratch root, filled by the packet when it opens a
lease; this module asks the Guard for the level's ceiling with that very literal as the scratch
root, so the two never drift apart: a change to what a level permits changes what a device is
told. `grants_read` asks the same strings the other way round: whether they let a Warden read
under a directory, and `readable_under` applies it to a list, which is how the directories the
operator names for watching are held to what the device itself would allow, by the Queen before
she sends them and again by the Warden that lives on the device.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.enrolment`.
    Called by the enrolment decision (`accept`) when a node is created and by the gateway when
    the operator changes a node's access level. Calls into hivemind.guard only.

Key invariants:
    - The result is a subset of `ceiling_for(level, ...)` plus `watch:<cell_id>`: nothing here
      can grant a device more than the Guard allows that level.
    - `READ_ONLY` grants `fs:read:**` and `watch:<cell_id>` and nothing else, so a read-only
      device runs no command and writes no file.
    - Every string parses with `Capability.parse`, and the result is sorted so two calls agree
      byte for byte.

See Also:
    - docs/waggle/enrolment.md section 7.1 for the table this reproduces.
    - hivemind.guard.access for the per-level ceilings and the `{scratch}` placeholder.
    - docs/waggle/gateway-contract.md section 10 for how the packet matches them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from pydantic import ValidationError

from hivemind.cell import AccessLevel
from hivemind.guard import (
    SCRATCH_PLACEHOLDER,
    Capability,
    CapabilityFamily,
    CapabilitySet,
    ceiling_for,
)
from waggle.ids import CellId

# The families a gateway can enforce on the machine itself; every other family is the Hive
# Stand's to enforce (module docstring). `watch` is added separately: it is not an access-level
# grant, it names the one Cell the Warden may observe.
PACKET_FAMILIES = frozenset(
    {
        CapabilityFamily.EXEC,
        CapabilityFamily.FS_READ,
        CapabilityFamily.FS_WRITE,
        CapabilityFamily.CELL_OUTSIDE_SCRATCH,
    }
)

__all__ = ["PACKET_FAMILIES", "granted_capabilities", "grants_read", "readable_under"]


def granted_capabilities(level: AccessLevel, cell_id: CellId) -> tuple[str, ...]:
    """Return the capability strings an `EnrolAccept` or `NodeUpdate` carries for `level`.

    Args:
        level: The access level granted to the device.
        cell_id: The device's Real Cell, which `watch:<cell_id>` names.

    Returns:
        The level's ceiling restricted to the packet's five families, with `{scratch}` left in
        place for the packet to fill per lease, sorted.
    """
    # The Guard fills `{scratch}` from a root path; handing it the placeholder itself as the root
    # makes it write the placeholder back, so the packet receives the template it expects.
    ceiling = ceiling_for(level, PurePosixPath(SCRATCH_PLACEHOLDER))
    kept = {str(capability) for capability in ceiling if capability.family in PACKET_FAMILIES}
    # Watching one's own Cell is allowed at every level: it is read-only by construction, and
    # the packet checks the capability again on every swarm.observe (ADR-0054).
    kept.add(str(Capability(family=CapabilityFamily.WATCH, scope=cell_id)))
    return tuple(sorted(kept))


def grants_read(granted: Sequence[str], root: str) -> bool:
    """Say whether a device's granted capability strings let its Warden read under `root`.

    The packet checks `fs:read` for every directory a `swarm.observe` names and refuses the whole
    request when one is not covered, so a directory the strings do not cover must never be asked
    for: asking would cost the Warden its process list and resource use too.

    Args:
        granted: The node's `granted_capabilities`, as the packet enforces them.
        root: A directory on the device, in the device's own spelling.

    Returns:
        True when some `fs:read` string covers `root` (every access level grants `fs:read:**`).
    """
    # The strings were built by `granted_capabilities` (or stored from it), so they parse; a
    # `root` the Guard's grammar refuses is not something any string could cover.
    try:
        needed = Capability(family=CapabilityFamily.FS_READ, scope=root)
    except ValidationError:
        return False
    return CapabilitySet.parse(*granted).allows(needed)


def readable_under(granted: Sequence[str], roots: Sequence[str]) -> tuple[str, ...]:
    """Keep the directories of `roots` that `granted` lets a Warden read, in their order.

    Args:
        granted: A device's granted capability strings.
        roots: Directories on the device, in its own spelling, as the operator named them.

    Returns:
        The directories `grants_read` accepts; every one at any access level today, since each
        grants `fs:read:**`, but the rule is the packet's own and a narrower grant would shorten
        the list instead of failing every look.
    """
    return tuple(root for root in roots if grants_read(granted, root))
