"""Define NucDemotion: stop a device's model server and make it an ordinary colonized Cell again.

The way down from a Nuc is the way up reversed, and shorter. The Queen orders the Warden to stop
its model server (`swarm.nuc_promote(DEMOTE)`); the Warden withdraws its model sources from the
Hive (`forage.source_removed`), stops the process and answers `swarm.nuc_promoted`. Only then does
the registry's rung move down (`swarm.demoted`, written with the change), so a device is never
recorded as colonized with a server still running. A demotion that cannot be confirmed (the Warden
is silent, the link is lost, or it answers FAILED) changes nothing and is recorded as a failure:
the device stays a Nuc until the operator asks again.

What the Nuc left in the Hive is tidied once the rung has moved. The Warden's allowlist of models
is emptied, so nothing it could load survives the server; the device's model sources are taken off
the Queen's map and their seats zeroed straight away, because the Warden's own `source_removed`
frames are a moment behind its reply and a plan written before they land would still put a gone
model first; and the hosting plan is written again, from the shared pool alone.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Started by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer` as a task of the gate's group. Calls into `hivemind.queen.forage`, `hivemind.queen.
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

__all__ = ["NucDemotion"]


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
        await self._lower()
        await self._tidy()

    async def _lower(self) -> None:
        """Move the rung down, update the hub's record and tell the device's packet."""
        env, node = self._env, self._node
        event = level_event(env.listener.recorder, node, RuntimeLevel.COLONIZED, self._reason)
        lowered = await env.registry.set_level(node.device_id, RuntimeLevel.COLONIZED, event)
        env.listener.hub.update_node(lowered)
        link = env.listener.hub.link(node.device_id)
        if link is not None:
            await tell_device(link, lowered, "The device is no longer a Nuc.")

    async def _tidy(self) -> None:
        """Empty the allowlist, take the device's sources off the map, and plan from the pool."""
        deps = self._env.context.deps
        try:
            await self._take_sources_off_the_map()
            current = deps.ledger.decisions.ceilings_for(self._node.warden_id)
            if current is not None:
                emptied = current.model_copy(update={"loadable_sources": ()})
                await change_ceilings(self._warden, emptied, deps)
            await write_hosting_plan(self._warden.cell, deps, self._warden)
        except (SwarmError, sqlite3.Error) as error:
            # The server is stopped and the rung moved; the next change writes these again.
            log.error(
                "swarm.nuc_tidy_failed",
                device_id=self._node.device_id,
                error=type(error).__name__,
            )

    async def _take_sources_off_the_map(self) -> None:
        """Remove every source the device hosted from the Queen's map and zero its seats."""
        deps, cell_id = self._env.context.deps, self._node.cell_id
        for source in deps.map.sources():
            if source.spec.host_cell_id == cell_id:
                await deps.map.remove_source(source.source_id)
                await deps.ledger.seats.set_capacity(source.source_id, 0)
