"""Hold a lost Swarm device's tasks, wait out the grace, and decide what becomes of them.

A Swarm device is somebody's machine and usually comes back, so the Queen does not fail what ran on
it the moment its link drops (ADR-0053). She holds its Cell's tasks for `[swarm]
unreachable_grace_s`, asks for a diagnosis from the Hive Stand's end, and then decides by what the
device can do alone: a Level 0 or Level 1 device's tasks are taken back and placed afresh, from
their last Handoff, because its hands are cut off from their Warden or its Warden has clustered;
a Nuc's tasks keep waiting, up to `[supervision] max_offline_s`, because it is still working on its
own pool. A device that returns inside the window cancels the hold. `.devices` is the hold itself,
`.decide` the pure rules of the grace and the verdicts, and `.diagnosis` the seam where the
Hive Stand's diagnostic Drone plugs in (a later wave's work). A package, because the hold, its
rules and the seam are three concepts and the first would outgrow one file (codingrules 5.2).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Built by the gate and called by its reconcile pass and by the device Warden door. Calls into
    `hivemind.queen.attach`, `hivemind.queen.swarm_gate` (takeback, terms, wardens),
    `hivemind.queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the hold and the decision.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the diagnosis the seam awaits.

Public API:
    - UnreachableDevices, HoldState, NO_LINK_REASON, WARDEN_LINK_REASON: the hold (devices).
    - CANCEL_GRACE_S: what a returning Warden's bee gets to checkpoint (effects).
    - Verdict, verdict_for, grace_over, offline_over: the pure rules (decide).
    - Diagnosis, NoDiagnosis: the diagnosis seam (diagnosis).
"""

from hivemind.queen.swarm_gate.unreachable.decide import (
    Verdict,
    grace_over,
    offline_over,
    verdict_for,
)
from hivemind.queen.swarm_gate.unreachable.devices import (
    NO_LINK_REASON,
    WARDEN_LINK_REASON,
    HoldState,
    UnreachableDevices,
)
from hivemind.queen.swarm_gate.unreachable.diagnosis import Diagnosis, NoDiagnosis
from hivemind.queen.swarm_gate.unreachable.effects import CANCEL_GRACE_S

__all__ = [
    "CANCEL_GRACE_S",
    "NO_LINK_REASON",
    "WARDEN_LINK_REASON",
    "Diagnosis",
    "HoldState",
    "NoDiagnosis",
    "UnreachableDevices",
    "Verdict",
    "grace_over",
    "offline_over",
    "verdict_for",
]
