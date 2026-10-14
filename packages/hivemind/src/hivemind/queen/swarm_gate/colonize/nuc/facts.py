"""Define nuc_decision, nuc_ceilings and restore_order: the pure halves of a device's model server.

The Queen's flows for the second rung are effectful from end to end (they send orders, wait and
write), so the judgements they make are kept here as pure functions of data, testable as tables.
`nuc_facts` gathers the facts about a device that might become a Nuc (what its Warden declared, what
the Hive allows, how much VRAM it has free, and the two things the Queen's own reasons weigh: the
pressure on the shared seats and whether a task on the device must survive a lost link) and
`nuc_decision` gives them to `decide_nuc` when the operator asks for the rung; the Queen's own
evaluation of a device gives the same facts to `decide_nuc` itself. `nuc_ceilings` turns an accepted
decision into the `Ceilings` the Queen sets once (codingrules 8.10): the VRAM and disk the device
has free, and the models the Warden may load by itself, all of the server's that fit. It exports no
seats: a device's model server answers on its own loopback, so no other Cell could use a seat lent
from it. `restore_order` is the third judgement, for a Nuc whose Warden restarted and so runs no
server: which server to start again and which models first. It reads what the Queen set for the
Warden rather than what the device has free now, because the same Warden once held the models that
would no longer fit beside themselves, and what it may load is already decided.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Called by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer` and `hivemind.queen.swarm_gate.rungs` (the facts), `.promote` (the ceilings) and
    `.restore` (the order). Calls into `hivemind.forage`, `hivemind.queen.swarm_gate.terms` and
    `hivemind.swarm` only.

Key invariants:
    - Pure: no I/O and no clock; the same node, declaration and terms give the same answer.
    - The facts carry the pressure and the need to outlive a link only when the caller has them;
      an operator's request carries neither, since its own word is the first reason.
    - The ceilings change only what a Nuc needs (VRAM, disk, the allowlist); the sub-bee cap and
      every other bound stay what the Queen already set.
    - A restore order names only a server the Hive allows and models the Queen let the Warden load.

See Also:
    - hivemind.swarm.nuc for ``decide_nuc``, which this feeds.
    - .claude/codingrules.md section 8.10 for "ceilings, not approvals".
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage import Ceilings
from hivemind.queen.swarm_gate.terms import GateTerms
from hivemind.swarm import (
    NucDecision,
    NucFacts,
    OfferedServer,
    RuntimeLevel,
    SwarmNode,
    decide_nuc,
    fit_together,
)
from hivemind.swarm.nuc import free_vram_bytes

__all__ = ["RestoreOrder", "nuc_ceilings", "nuc_decision", "nuc_facts", "restore_order"]


@dataclass(frozen=True, slots=True)
class RestoreOrder:
    """What to tell a restarted Warden to start again.

    Attributes:
        server_id: The model server to start.
        initial: The models to load first, in the order the Hive lists them.
    """

    server_id: str
    initial: tuple[str, ...]


def nuc_facts(
    node: SwarmNode,
    declared: tuple[str, ...],
    terms: GateTerms,
    *,
    pressure: float = 0.0,
    survives: bool = False,
) -> NucFacts:
    """Gather what `decide_nuc` weighs about `node` and the Hive.

    Args:
        node: The device, with the capacity it last reported and any wish of the operator's.
        declared: The model server ids its Warden declared in its capacity report.
        terms: The gate's numbers: the seat pressure and the servers the Hive allows.
        pressure: The Hive's seat pressure now (`rungs.pressure.seat_pressure`); 0 when the
            operator asked for the rung, since its own word is the first reason.
        survives: Whether a task placed on or pinned to the device must survive a lost link.

    Returns:
        The facts.
    """
    return NucFacts(
        level=node.level,
        capacity=node.capacity,
        declared=declared,
        offered=terms.servers,
        desired_level=node.desired_level,
        hive_pressure=pressure,
        seat_pressure=terms.nuc_seat_pressure,
        needs_outage_survival=survives,
    )


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
    return decide_nuc(nuc_facts(node, declared, terms))


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


def restore_order(
    ceilings: Ceilings | None, terms: GateTerms, declared: tuple[str, ...]
) -> RestoreOrder | None:
    """Return the server and models to start again on a Nuc whose Warden restarted, if any.

    Args:
        ceilings: What the Queen set for the Warden when she made the device a Nuc; None if she
            holds none (a Warden she never promoted).
        terms: The gate's numbers: the servers the Hive allows and the models it lists for each.
        declared: The server ids the Warden declared; empty before its capacity report arrived,
            when any server the Hive allows may be tried and the Warden refuses one it lacks.

    Returns:
        The first server, declared servers first in the order the Warden listed them, that has a
        model the Queen let the Warden load, with the models that fit together in the VRAM she
        allowed it; None when no server qualifies, so there is nothing to start again.
    """
    if ceilings is None:
        return None
    allowed = set(ceilings.loadable_sources)
    for server in _candidates(terms.servers, declared):
        mine = tuple(source for source in server.sources if source.source_id in allowed)
        initial = fit_together(mine, ceilings.model_vram_bytes)
        if initial:
            return RestoreOrder(server.server_id, initial)
    return None


def _candidates(
    offered: tuple[OfferedServer, ...], declared: tuple[str, ...]
) -> list[OfferedServer]:
    """The servers worth trying: the declared ones the Hive allows, in the Warden's order."""
    if not declared:
        return list(offered)  # Nothing declared yet: the Warden itself refuses one it lacks.
    by_id = {server.server_id: server for server in offered}
    return [by_id[server_id] for server_id in declared if server_id in by_id]
