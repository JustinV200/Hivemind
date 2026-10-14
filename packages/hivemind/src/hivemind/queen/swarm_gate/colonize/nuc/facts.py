"""Define nuc_decision and nuc_ceilings: the pure halves of promoting a device to a Nuc.

The Queen's flow for the second rung is effectful from end to end (it sends orders, waits and
writes), so the two judgements it makes are kept here as pure functions of data, testable as
tables. `nuc_decision` gathers the facts about a device that asks for the rung (what its Warden
declared, what the Hive allows, how much VRAM it has free) and gives them to `decide_nuc`, which
answers with a server and its models, or one word for why not; a device that is not asking for it
gets no decision at all. `nuc_ceilings` turns an accepted decision into the `Ceilings` the Queen
sets once (codingrules 8.10): the VRAM and disk the device has free, and the models the Warden may
load by itself, all of the server's that fit. It exports no seats: a device's model server answers
on its own loopback, so no other Cell could use a seat lent from it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Called by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer` (the decision) and `.promote` (the ceilings). Calls into `hivemind.forage`,
    `hivemind.queen.swarm_gate.terms` and `hivemind.swarm` only.

Key invariants:
    - Pure: no I/O and no clock; the same node, declaration and terms give the same answer.
    - Pressure and a task's need to outlive its link are not wired as triggers yet: the facts
      carry none, so only the operator's own request promotes a device.
    - The ceilings change only what a Nuc needs (VRAM, disk, the allowlist); the sub-bee cap and
      every other bound stay what the Queen already set.

See Also:
    - hivemind.swarm.nuc for ``decide_nuc``, which this feeds.
    - .claude/codingrules.md section 8.10 for "ceilings, not approvals".
"""

from __future__ import annotations

from hivemind.forage import Ceilings
from hivemind.queen.swarm_gate.terms import GateTerms
from hivemind.swarm import (
    NucDecision,
    NucFacts,
    RuntimeLevel,
    SwarmNode,
    decide_nuc,
)
from hivemind.swarm.nuc import free_vram_bytes

__all__ = ["nuc_ceilings", "nuc_decision"]


def nuc_decision(
    node: SwarmNode, declared: tuple[str, ...], terms: GateTerms
) -> NucDecision | None:
    """Decide whether `node` should become a Nuc, when it is a colonized device asking to.

    Args:
        node: The device, with the capacity it last reported.
        declared: The model server ids its Warden declared in its capacity report.
        terms: The gate's numbers: the seat pressure and the servers the Hive allows.

    Returns:
        The decision; None for a device that is not on the second rung asking for the third, which
        needs no Nuc decision at all.
    """
    if node.level is not RuntimeLevel.COLONIZED or node.desired_level is not RuntimeLevel.NUC:
        return None
    facts = NucFacts(
        level=node.level,
        capacity=node.capacity,
        declared=declared,
        offered=terms.servers,
        desired_level=node.desired_level,
        hive_pressure=0.0,  # Pressure-driven promotion is the Queen's own trigger, not wired yet.
        seat_pressure=terms.nuc_seat_pressure,
    )
    return decide_nuc(facts)


def nuc_ceilings(current: Ceilings, node: SwarmNode, decision: NucDecision) -> Ceilings:
    """Return `current` with the bounds a Nuc's model server needs.

    Args:
        current: The ceilings the Warden holds now (or a new Warden's first).
        node: The device, with the capacity it last reported.
        decision: An accepted promotion: its `loadable` models are the new allowlist.

    Returns:
        The ceilings to set: the device's free VRAM and disk for models, and the allowlist.
    """
    return current.model_copy(
        update={
            "model_vram_bytes": free_vram_bytes(node.capacity),
            "model_disk_bytes": node.capacity.host.disk_free_bytes,
            "loadable_sources": decision.loadable,
        }
    )
