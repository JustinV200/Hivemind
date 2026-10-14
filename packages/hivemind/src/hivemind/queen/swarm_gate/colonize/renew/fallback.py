"""Define fall_back_to_level0: leave a device a Level 0 one when its Warden could not be renewed.

The Queen restarts a colonized device's Warden so that it runs under the terms the operator set,
and the restart can fail: the packet could not stop the old Warden, could not start a new one, the
new one never dialled in, or it came up under the old terms after all. The old Warden may still be
running under terms wider than the operator now allows, and a device must never be left like that,
so a renewal that fails falls back to the one place the Queen can always enforce the registry's
terms: the Hive Stand. The packet is asked to stop any Warden it started, the Warden that dialled
in from the device is let go, the rung is lowered to the first one (down through the colonized rung
for a Nuc, whose sources are taken off the Queen's map), and the Level 0 Warden is built from the
registry's row, so it carries the new terms by construction. The tasks the renewal paused resume
there from their Handoffs, so no task fails for it. The operator can colonize the device again once
whatever failed is put right.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.renew`. Called by `.flow.Renewal` when a step fails.
    Calls into `hivemind.queen.swarm_gate` (its own modules), `hivemind.queen.trail`,
    `hivemind.swarm` and waggle only.

Key invariants:
    - A packet that cannot be reached is not asked again: its words (`link_lost`, `packet_silent`)
      skip the WITHDRAW, which would only wait out another deadline.
    - The rung is lowered one edge at a time, because the ladder has no edge from a Nuc to Level 0.
    - A device the operator revoked meanwhile gets no Warden; its paused tasks are taken back.
    - A task is never failed by the fallback: it resumes on the Hive Stand's Warden, or goes back
      to PENDING with the reason on the trail.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.flow for the renewal that falls back.
    - hivemind.queen.swarm_gate.colonize.promote for the undo of a colonization, which this mirrors.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.colonize.events import level_event
from hivemind.queen.swarm_gate.colonize.nuc.demote import (
    lower_to_colonized,
    take_sources_off_the_map,
)
from hivemind.queen.swarm_gate.colonize.steps import (
    DEAD_LINK_WORDS,
    WITHDRAW_DEADLINE_S,
    Move,
    StepOrder,
    dismiss_remote,
    resume_all,
    send_order,
    tell_device,
)
from hivemind.queen.swarm_gate.takeback import take_back
from hivemind.queen.trail import record_event
from hivemind.swarm import NodeStatus, RuntimeLevel, SwarmNode
from waggle.messages.swarm import ColonizeAction

log = get_logger(__name__)

__all__ = ["fall_back_to_level0"]


async def fall_back_to_level0(env: FlowEnv, move: Move, word: str) -> None:
    """Make the device a Level 0 one under its present terms, and resume what the renewal paused.

    Args:
        env: The Queen, the Level 0 Wardens, the registry and the listener.
        move: The renewal's migration: the device, its packet link and the tasks it paused.
        word: The stable word for how the renewal failed.
    """
    node = move.node
    log.warning("swarm.renew_fell_back", device_id=node.device_id, word=word)
    reason = f"Terms renewal failed ({word}): the Warden returns to the Hive Stand."
    if word not in DEAD_LINK_WORDS:
        await _withdraw(move, reason)
    await dismiss_remote(env.context, env.wardens, node)
    deps = env.context.deps
    current = await env.registry.node(node.device_id)
    if current is None or current.status is NodeStatus.REVOKED:
        # Nothing to rebuild for a device the operator removed meanwhile; its tasks move on.
        await take_back(deps, node, "revoked_during_move", only=move.paused)
        return
    lowered = await _lower_to_gateway(env, current, reason)
    await record_event(deps, "queen.decided", node.device_id, reason="renew_failed", detail=word)
    if not await env.wardens.serve(lowered):
        await take_back(deps, lowered, "level0_not_rebuilt", only=move.paused)
        return
    stranded = await resume_all(env.context, move, reason)
    if stranded:
        await take_back(deps, lowered, "resume_failed", only=stranded)


async def _withdraw(move: Move, reason: str) -> None:
    """Ask the packet to stop any Warden it started; a packet that cannot answer is left."""
    step = StepOrder(ColonizeAction.WITHDRAW, WITHDRAW_DEADLINE_S, reason)
    try:
        await send_order(move.link, move.node, step)
    except ColonizeStepError as error:
        log.warning("swarm.renew_withdraw_failed", device_id=move.node.device_id, word=error.word)


async def _lower_to_gateway(env: FlowEnv, node: SwarmNode, reason: str) -> SwarmNode:
    """Lower the rung to Level 0, through the colonized rung from a Nuc; return the device."""
    if node.level is RuntimeLevel.NUC:
        await take_sources_off_the_map(env, node)  # Its server went with its Warden.
        node = await lower_to_colonized(env, node, reason)
    if node.level is RuntimeLevel.GATEWAY:
        return node
    event = level_event(env.listener.recorder, node, RuntimeLevel.GATEWAY, reason)
    lowered = await env.registry.set_level(node.device_id, RuntimeLevel.GATEWAY, event)
    env.listener.hub.update_node(lowered)
    link = env.listener.hub.link(node.device_id)
    if link is not None:
        await tell_device(link, lowered, "The Warden returned to the Hive Stand.")
    return lowered
