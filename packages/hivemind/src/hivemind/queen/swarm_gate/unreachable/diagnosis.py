"""Define Diagnosis and NoDiagnosis: the seam where the Hive Stand's diagnostic Drone plugs in.

While the Queen holds a lost device's tasks she asks for a diagnosis (ADR-0056, roadmap 11.9a): for
a colonized device, the Hive Stand's own Warden spawns a short-lived Drone on its local pool to
check reachability from this end (DNS and route to the device, whether other nodes are affected
too, ruling out "my network" versus "that device"). This is the place it attaches: the hold calls
`begin` when it starts and `end` when it is over, whichever way it ended, and passes the node and
the short stable reason the link was lost. `hivemind.queen.swarm_gate.unreachable.hive_stand` is
what the Hive installs; a hold with no diagnosis installed runs with `NoDiagnosis`, which does
nothing, so the timeline the Queen follows (hold, grace, decide) is complete and testable without
the Drone.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.unreachable`. Called by `.devices` at the start and the end of a
    hold; implemented by the Hive Stand-side diagnosis in `.hive_stand` (roadmap 11.9a). Calls into
    `hivemind.swarm` (the node type) only.

Key invariants:
    - `begin` and `end` are called once per hold each, in that order; `end` is called whether the
      device came back or the Queen decided.
    - Neither may raise for a diagnosis that finds nothing: the hold's timeline never depends on
      the diagnosis succeeding.
    - A diagnosis learns a node and a reason word, never an address, a key or a path.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for what a diagnosis is.
    - hivemind.queen.swarm_gate.unreachable.devices for the hold that calls it.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.swarm import SwarmNode

__all__ = ["Diagnosis", "NoDiagnosis"]


class Diagnosis(Protocol):
    """Diagnose a lost device from the Hive Stand's end while the Queen holds its tasks."""

    async def begin(self, node: SwarmNode, reason: str) -> None:
        """Start diagnosing why `node` is unreachable.

        Args:
            node: The device that was lost.
            reason: The short stable word the loss was reported with (`closed`,
                `missed_heartbeats`, `warden_link_closed`).
        """
        ...

    async def end(self, node: SwarmNode) -> None:
        """Stop diagnosing `node`: it is back, or the Queen has decided.

        Args:
            node: The device whose diagnosis is over.
        """
        ...


class NoDiagnosis:
    """The diagnosis of a Hive with no diagnostic Drone yet: it asks nothing and finds nothing."""

    async def begin(self, node: SwarmNode, reason: str) -> None:
        """Do nothing: no Drone is spawned."""

    async def end(self, node: SwarmNode) -> None:
        """Do nothing: there is no diagnosis to stop."""
