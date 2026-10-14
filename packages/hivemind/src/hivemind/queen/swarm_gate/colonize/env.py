"""Define FlowEnv: everything one colonization flow works with, bundled once.

A flow needs the Queen and her collaborators, the table of Level 0 Wardens it stops and rebuilds,
the registry it moves the rung in, the listener whose hub reaches the device and whose address the
started Warden dials, the gate's numbers, and the place the Warden's arrival is reported. That is
six things, and every step of every flow takes some of them, so they travel as one frozen value
(codingrules 5.1) built when the gate starts, instead of as a long argument list threaded through
each helper.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Built by `.flow.Colonizer` when the gate binds the
    Queen, and read by `.promote` and `.withdraw`. Calls into `hivemind.queen.swarm_gate` (its own
    types) and `hivemind.swarm` only.

Key invariants:
    - Nothing here is live state of its own: it holds references, so two flows of two devices
      share the same tables and the same arrivals, never copies.
    - Frozen: a flow cannot rebind the registry or the listener under another flow.

See Also:
    - hivemind.queen.swarm_gate.colonize.flow for where it is built.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.queen.swarm_gate.colonize.arrivals import Arrivals
from hivemind.queen.swarm_gate.terms import GateContext, GateTerms
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import SwarmListener, SwarmRegistry

__all__ = ["FlowEnv"]


@dataclass(frozen=True, slots=True)
class FlowEnv:
    """What a colonization flow works with.

    Attributes:
        context: The Queen and her collaborators.
        wardens: The Level 0 Wardens: stopped when one's device is colonized, rebuilt when the move
            is undone or the device is withdrawn.
        registry: Where the device's rung, and the operator's pending request, are kept.
        listener: The swarm listener: its hub reaches the device, its recorder writes the
            `swarm.*` events and its address is the one the started Warden dials.
        terms: The gate's numbers: the runtime version to install, and the colonization limits.
        arrivals: Where the door reports that the device's Warden attached.
    """

    context: GateContext
    wardens: DeviceWardens
    registry: SwarmRegistry
    listener: SwarmListener
    terms: GateTerms
    arrivals: Arrivals
