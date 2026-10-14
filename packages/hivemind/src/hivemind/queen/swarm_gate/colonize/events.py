"""Build the trail events a colonization writes: a rung changed, a Warden moved, a move undone.

A rung change is a `swarm.promoted` or a `swarm.demoted` with the rung it left and the rung it
reached (codingrules Appendix C), and the registry writes it in the same transaction as the change,
so the event is built here and handed to `set_level`, never recorded on its own. A Warden's move
from the Hive Stand's node to the device's is `warden.migrated`: the id is the same, the signing
node is not, and the Guard Bee learns the new node's Cell from it. A move that failed and was
undone is a `swarm.demoted` too, from the rung the Queen tried for back to the one the device never
left, with the reason as a stable word, because the device was a rung higher for as long as its
Warden was on its way. All carry ids and words only (codingrules 12): no address, key or path.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Called by `.flow` as each step lands. Calls into
    `hivemind.pheromone`, `hivemind.queen.deps` (the Queen's identity and trail),
    `hivemind.swarm` (the recorder and the ladder) and waggle only.

Key invariants:
    - A level event names `from_level` and `to_level` as the ladder's wire values.
    - Building an event writes nothing; only `record_migrated` writes, and a failure there
      propagates.
    - `warden.migrated` names the same Warden before and after: it carries `from_node` and
      `to_node`, and never a second Warden id.

See Also:
    - hivemind.swarm.registry.protocol for `set_level`, which writes the event with the change.
    - hivemind.workers.roles.guard_bee.facts for how `warden.migrated` re-binds a node to a Cell.
"""

from __future__ import annotations

from hivemind.pheromone import SwarmEvent, WardenEvent
from hivemind.queen.deps import QueenDeps
from hivemind.swarm import RuntimeLevel, SwarmNode, SwarmRecorder, is_promotion
from waggle.ids import NodeId, new_event_id

__all__ = ["level_event", "record_migrated", "undone_event"]


def level_event(
    recorder: SwarmRecorder, node: SwarmNode, to_level: RuntimeLevel, reason: str
) -> SwarmEvent:
    """Build the `swarm.promoted` or `swarm.demoted` for moving `node` to `to_level`.

    Args:
        recorder: The Swarm's writer, whose identity and clock stamp the event.
        node: The device as it is before the change.
        to_level: The rung it is moving to.
        reason: Why, in one short sentence.

    Returns:
        The event, for the registry to write with the change.
    """
    kind = "swarm.promoted" if is_promotion(node.level, to_level) else "swarm.demoted"
    payload = {
        "device_id": node.device_id,
        "node_id": node.node_id,
        "cell_id": node.cell_id,
        "from_level": node.level.value,
        "to_level": to_level.value,
        "reason": reason,
    }
    return recorder.swarm_event(kind, node.device_id, payload)


def undone_event(recorder: SwarmRecorder, node: SwarmNode, word: str) -> SwarmEvent:
    """Build the `swarm.demoted` for a colonization that failed and was rolled back.

    Args:
        recorder: The Swarm's writer.
        node: The device, still on the rung it never left.
        word: The stable word for how the move failed.

    Returns:
        The event: from the rung the Queen tried for, back to the device's own.
    """
    payload = {
        "device_id": node.device_id,
        "node_id": node.node_id,
        "cell_id": node.cell_id,
        "from_level": RuntimeLevel.COLONIZED.value,
        "to_level": node.level.value,
        "reason": word,
    }
    return recorder.swarm_event("swarm.demoted", node.device_id, payload)


async def record_migrated(deps: QueenDeps, node: SwarmNode, from_node: NodeId) -> None:
    """Record `warden.migrated`: the device's Warden now signs as the device's node.

    Args:
        deps: The Queen's collaborators; her trail, clock and identity write the event.
        node: The device, now on the rung that holds its Warden.
        from_node: The node the Warden signed as before (the Hive Stand's).
    """
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="warden.migrated",
        subject_id=node.warden_id,
        payload={
            "cell_id": node.cell_id,
            "device_id": node.device_id,
            "from_node": from_node,
            "to_node": node.node_id,
        },
    )
    await deps.trail.record(event)
