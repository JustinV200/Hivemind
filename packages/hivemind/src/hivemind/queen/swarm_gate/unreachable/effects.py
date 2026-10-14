"""Define the effects a hold has on a lost device: mark it, let its Warden go, cancel doubles.

The hold (`.devices`) decides when; these are the things it then does to the device and its Warden,
kept apart so the hold reads as its timeline and each effect is one function with one reason to
change. A device whose own Warden is lost is marked UNREACHABLE (`swarm.unreachable`), because its
packet's heartbeats would otherwise go on calling it online. A Warden the Queen gives up on is let
go: a Level 0 Warden is stopped, a device Warden's dead link is detached and closed. A device
Warden that comes back after its tasks were placed elsewhere is told to cancel them, so nothing
runs twice. Every event carries ids and a reason word, never an address, a key or a path.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.unreachable`. Called by `.devices`. Calls into
    `hivemind.queen.attach`, `hivemind.queen.swarm_gate` (terms, wardens), `hivemind.swarm` and
    waggle only.

Key invariants:
    - Marking a node never raises for a node another writer already moved: it was recorded by
      that writer.
    - Only an ONLINE node is marked: one already UNREACHABLE (its packet is gone too) or revoked
      is left as it is.
    - Cancelling sends nothing to a Warden that is not attached, or when nothing was abandoned.

See Also:
    - hivemind.queen.swarm_gate.unreachable.devices for the hold that applies them.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the rules they carry out.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from hivemind.queen.attach import detach_warden
from hivemind.queen.swarm_gate.terms import GateContext
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import (
    NodeStatus,
    NodeStatusConflictError,
    RuntimeLevel,
    SwarmNode,
    SwarmRecorder,
    SwarmRegistry,
)
from waggle.envelope import wrap
from waggle.ids import TaskId
from waggle.messages.task import TaskCancel

CANCEL_GRACE_S = 5.0  # What a returning Warden's bee gets to checkpoint before it is stopped.

__all__ = ["CANCEL_GRACE_S", "cancel_abandoned", "event_ids", "let_go", "mark_unreachable"]


def event_ids(node: SwarmNode, reason: str) -> dict[str, JsonValue]:
    """Return the ids and the reason a hold's events carry: no address, key or path."""
    return {
        "device_id": node.device_id,
        "node_id": node.node_id,
        "cell_id": node.cell_id,
        "reason": reason,
    }


async def mark_unreachable(
    context: GateContext,
    registry: SwarmRegistry,
    recorder: SwarmRecorder,
    node: SwarmNode,
    reason: str,
) -> None:
    """Mark a node whose Warden is gone UNREACHABLE; its heartbeats would call it online.

    Args:
        context: The Queen, whose clock stamps the change.
        registry: Where the node's status is moved, with its `swarm.unreachable` event.
        recorder: Builds that event.
        node: The device whose Warden was lost.
        reason: The short stable word the loss was reported with.
    """
    if node.status is not NodeStatus.ONLINE:
        return  # Already unreachable (its packet's link is gone too), or revoked.
    event = recorder.swarm_event("swarm.unreachable", node.device_id, event_ids(node, reason))
    try:
        await registry.set_status(
            node.device_id,
            NodeStatus.ONLINE,
            NodeStatus.UNREACHABLE,
            context.deps.clock.now(),
            event,
        )
    except NodeStatusConflictError:
        return  # Another writer moved it first and recorded its own change.


async def let_go(context: GateContext, wardens: DeviceWardens, node: SwarmNode) -> None:
    """Stop a Level 0 Warden, or detach and close a device Warden's dead link.

    Args:
        context: The Queen the Warden is attached to.
        wardens: The Level 0 Wardens, and where a device Warden's link is found.
        node: The device whose Warden the Queen gives up on.
    """
    if node.level is RuntimeLevel.GATEWAY:
        await wardens.stop(node.device_id)
        return
    link = wardens.link_of(node.warden_id)
    if link is not None:
        await detach_warden(context.queen, node.warden_id)
        await link.transport.close()


async def cancel_abandoned(
    context: GateContext, wardens: DeviceWardens, node: SwarmNode, abandoned: Sequence[TaskId]
) -> None:
    """Tell a returning device Warden to stop the tasks the Queen placed elsewhere meanwhile.

    Args:
        context: The Queen, whose clock stamps the orders.
        wardens: Where the returning Warden's link is found.
        node: The device whose Warden is back.
        abandoned: The tasks taken back while it was away.
    """
    link = wardens.link_of(node.warden_id)
    if link is None or not abandoned:
        return
    reason = "The Queen placed this task elsewhere while its device was away."
    for task_id in abandoned:
        cancel = TaskCancel(task_id=task_id, grace_s=CANCEL_GRACE_S, reason=reason)
        await link.send(wrap(cancel, link.hop, clock=context.deps.clock))
