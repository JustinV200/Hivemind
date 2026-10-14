"""Define NucDemotion: stop a device's model server and make it an ordinary colonized Cell again.

The way down from a Nuc is the way up reversed, and shorter. The Queen orders the Warden to stop
its model server (`swarm.nuc_promote(DEMOTE)`); the Warden withdraws its model sources from the
Hive (`forage.source_removed`), stops the process and answers `swarm.nuc_promoted`. Only then does
the registry's rung move down (`swarm.demoted`, written with the change), so a device is never
recorded as colonized with a server still running. A demotion that cannot be confirmed (the Warden
is silent, the link is lost, or it answers FAILED) changes nothing and is recorded as a failure:
the device stays a Nuc until it is asked again, by the operator or by the Queen herself once her
back-off has run out.

What the Nuc left in the Hive is tidied once the rung has moved. The Warden's allowlist of models
is emptied, so nothing it could load survives the server; the device's model sources are taken off
the Queen's map and their seats zeroed straight away, because the Warden's own `source_removed`
frames are a moment behind its reply and a plan written before they land would still put a gone
model first; and the hosting plan is written again, from the shared pool alone.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Started by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer` as a task of the gate's group, for the operator's request or the Queen's own
    decision; its lowering and tidying are shared with `.restore`, which lowers a Nuc whose server
    could not be started again. Calls into `hivemind.queen.forage`, `hivemind.queen.
    swarm_gate.colonize` (its own steps), `hivemind.swarm` and waggle only.

Key invariants:
    - The registry's rung moves down only after the Warden confirmed its server is stopped.
    - A demotion that fails changes nothing but the record of the failure.
    - Taking sources off the Queen's map is idempotent with the Warden's own removals.
    - A failure to tidy after the rung moved is logged, not undone: the server is stopped.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.promote for the way up.
    - hivemind.wardens.ticks.nuc for the Warden's side of the order.
"""

from __future__ import annotations

import sqlite3

from hivemind.common.logging import get_logger
from hivemind.queen.deps import WardenLink
from hivemind.queen.forage import change_ceilings, write_hosting_plan
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.colonize.events import level_event
from hivemind.queen.swarm_gate.colonize.nuc.promote import DEMOTE_DEADLINE_S
from hivemind.queen.swarm_gate.colonize.steps import tell_device
from hivemind.swarm import RuntimeLevel, SwarmError, SwarmNode
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote

log = get_logger(__name__)

__all__ = ["NucDemotion", "lower_to_colonized", "take_sources_off_the_map", "tidy_nuc"]


async def lower_to_colonized(env: FlowEnv, node: SwarmNode, reason: str) -> SwarmNode:
    """Move a Nuc's rung down to the colonized one, update the hub's record and tell its packet.

    Args:
        env: The registry, the listener (its recorder and hub) and the Queen.
        node: The device on the Nuc rung, as the registry holds it.
        reason: Why, in one short sentence (a stable word when it is a failure).

    Returns:
        The device as the registry now holds it, on the colonized rung.
    """
    event = level_event(env.listener.recorder, node, RuntimeLevel.COLONIZED, reason)
    lowered = await env.registry.set_level(node.device_id, RuntimeLevel.COLONIZED, event)
    env.listener.hub.update_node(lowered)
    link = env.listener.hub.link(node.device_id)
    if link is not None:
        await tell_device(link, lowered, "The device is no longer a Nuc.")
    return lowered


async def take_sources_off_the_map(env: FlowEnv, node: SwarmNode) -> None:
    """Remove every source the device hosted from the Queen's map and zero its seats.

    Idempotent with the Warden's own `forage.source_removed` frames, which are a moment behind
    its reply: a plan written before they land would still put a gone model first.

    Args:
        env: The Queen, whose map and ledger are written.
        node: The device whose sources go.
    """
    deps = env.context.deps
    for source in deps.map.sources():
        if source.spec.host_cell_id == node.cell_id:
            await deps.map.remove_source(source.source_id)
            await deps.ledger.seats.set_capacity(source.source_id, 0)


async def tidy_nuc(env: FlowEnv, node: SwarmNode, warden: WardenLink) -> None:
    """Empty the allowlist, take the device's sources off the map, and plan from the shared pool.

    A failure is logged, not raised: the server is stopped and the rung moved, and the next change
    writes these again.

    Args:
        env: The Queen and her collaborators.
        node: The device that was a Nuc.
        warden: The Queen's link to the device's own Warden, which is sent the new bounds.
    """
    deps = env.context.deps
    try:
        await take_sources_off_the_map(env, node)
        current = deps.ledger.decisions.ceilings_for(node.warden_id)
        if current is not None:
            emptied = current.model_copy(update={"loadable_sources": ()})
            await change_ceilings(warden, emptied, deps)
        await write_hosting_plan(warden.cell, deps, warden)
    except (SwarmError, sqlite3.Error) as error:
        log.error("swarm.nuc_tidy_failed", device_id=node.device_id, error=type(error).__name__)


class NucDemotion:
    """Stop one Nuc's model server and lower the device to the colonized rung."""

    def __init__(self, env: FlowEnv, node: SwarmNode, warden: WardenLink, why: str) -> None:
        """Build the flow; `run` carries it out.

        Args:
            env: The Queen, the registry, the listener and the desk.
            node: The device, on the third rung, as the registry holds it.
            warden: The Queen's link to the device's own Warden.
            why: Who asked, in one word (the operator).
        """
        self._env = env
        self._node = node
        self._warden = warden
        self._reason = f"Nuc demotion ({why}): the device's model server stops."

    async def run(self) -> None:
        """Stop the server, lower the rung and tidy what the Nuc left behind.

        Raises:
            ColonizeStepError: The Warden could not be asked, did not answer or did not report
                DEMOTED (`link_lost`, `warden_silent`, `demote_failed`); nothing has changed.
        """
        node = self._node
        order = NucPromote(
            cell_id=node.cell_id,
            device_id=node.device_id,
            action=NucAction.DEMOTE,
            server_id=None,
            initial_models=(),
            deadline_s=DEMOTE_DEADLINE_S,
            reason=self._reason,
        )
        reply = await self._env.desk.ask(self._warden, order, self._env.context.deps.clock)
        if reply.outcome is not NucOutcome.DEMOTED:
            raise ColonizeStepError("demote_failed")
        await lower_to_colonized(self._env, node, self._reason)
        await tidy_nuc(self._env, node, self._warden)
