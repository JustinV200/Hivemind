"""Define terms_drifted: whether a Warden still runs under terms the operator has since changed.

A device's Warden is started with its access level and Comb Shield tier in its environment, from
the terms the packet held at that moment, and it keeps them for as long as it lives: it has no
message that changes them, and the commands it runs go through a lease of its own that the
packet's checks do not constrain. So when the operator runs `hive swarm access` or `shield` on a
device whose Warden is on it, the registry changes and the Warden does not. The Queen tells the two
apart by what the Warden said when it connected: its `cell.ready` names the terms it was started
under, and the gate's desk keeps them. This is the whole comparison, as a pure function of those
terms and the registry's row, so what counts as a difference is read in one place.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.renew`. Called by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer`, which starts a renewal when it holds. Calls into `hivemind.queen.swarm_gate.
    colonize.nuc` (the reported terms' type) and `hivemind.swarm` only.

Key invariants:
    - Pure: no I/O and no clock; the same terms give the same answer.
    - A Warden that has reported nothing is not drifted: nothing is known, so nothing is renewed.
    - Both dimensions count, the access level and the tier, and either direction: a widening
      reaches the Warden by restart just as a narrowing does.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.flow for what is done about a difference.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for a colonized device's terms.
"""

from __future__ import annotations

from hivemind.queen.swarm_gate.colonize.nuc.desk import Reported
from hivemind.swarm import SwarmNode

__all__ = ["terms_drifted"]


def terms_drifted(reported: Reported | None, node: SwarmNode) -> bool:
    """Whether the Warden's reported terms differ from the terms the registry holds for it.

    Args:
        reported: The terms the Warden connected with, or None when it has reported none.
        node: The device as the registry holds it now.

    Returns:
        True when the Warden reported an access level or a tier other than the registry's.
    """
    if reported is None:
        return False
    return (
        reported.access_level is not node.access_level
        or reported.comb_shield is not node.comb_shield
    )
