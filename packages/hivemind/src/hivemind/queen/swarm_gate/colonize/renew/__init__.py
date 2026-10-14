"""Make a colonized device's Warden run under the terms the operator set: the terms renewal.

A device's access level and Comb Shield tier are the operator's, and `hive swarm access` and
`shield` change them in the registry. A Level 0 device's Warden is on the Hive Stand, so the gate
rebuilds it from the registry's row. A colonized device's Warden is on the device and holds the
terms it was started with, so it is restarted instead: the packet is sent the terms at once, the
Warden's tasks are checkpointed, the packet stops and starts the Warden (which reads the terms it
now holds), a Nuc's model server is started again, and the tasks resume from their Handoffs. If
that fails the device falls back to Level 0, where the Queen can always enforce the registry's
terms. A package, because the comparison, the flow and the fallback are separate concepts that
together outgrow one file (codingrules 5.2), and `colonize` is at the fan-out limit.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Started by `colonize.flow.Colonizer` when a Warden's
    reported terms differ from the registry's. Calls into `hivemind.queen.swarm_gate` (its own
    modules), `hivemind.swarm` and waggle only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the choice of mechanism.
    - hivemind.queen.swarm_gate.reconcile for the same change at Level 0 (`_update`).
    - hivemind.queen.swarm_gate.colonize.nuc for the Nuc restoration this runs for a Nuc.

Public API:
    - Renewal, TERMS_CHANGED_REASON: the flow that restarts the Warden (flow).
    - terms_drifted: whether a Warden runs under terms the operator has changed (drift).
    - fall_back_to_level0: what a failed renewal leaves the device as (fallback).
"""

from hivemind.queen.swarm_gate.colonize.renew.drift import terms_drifted
from hivemind.queen.swarm_gate.colonize.renew.fallback import fall_back_to_level0
from hivemind.queen.swarm_gate.colonize.renew.flow import TERMS_CHANGED_REASON, Renewal

__all__ = ["TERMS_CHANGED_REASON", "Renewal", "fall_back_to_level0", "terms_drifted"]
