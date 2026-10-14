"""Define NucRestoration: start a Nuc's model server again after its Warden restarted, or lower it.

A Nuc is a colonized device whose Warden runs a model server, and the server is the Warden's child:
a Warden that restarted (it crashed, the device rebooted, the Queen restarted it for new terms, or
the Queen herself restarted and the Warden dialled in afresh) runs none, while the registry still
says the device is on the Nuc rung. Left like that the Queen would plan work onto models that are
not there. So whenever the Warden of a device recorded as a Nuc connects, a flow of this kind runs
before the rung is trusted again (`NucDesk.is_proven`), and it ends in one of two ways, never a
third. Either the server is up and the device is a Nuc again, or the registry is made to say what
is true, that the device is a colonized one.

The Queen asks the Warden to start the server with the same order that made it a Nuc, which is
idempotent on the Warden's side (a server already up only has any missing model offered). She names
the server and the models from what she set for the Warden when she made the device a Nuc (its
ceilings, which the attach has just sent it again), not from the device's free VRAM now, which is
a different question: the models are the ones she already allowed, and a Warden that survived the
Queen's own restart still holds them in memory. The hosting plan is then written again with the
device's own models first, since its sources are announced afresh by the Warden. If the Warden
answers that it cannot, or the Queen holds nothing to restore from, she asks it to stop anything it
started, lowers the rung (`swarm.demoted`, with the word that says why), takes the device's
sources off her map and plans from the shared pool, and records `queen.decided`. If the Warden
cannot be reached or does not answer, nothing is decided: the device stays unproven, and the next
time its Warden is reachable the flow runs again.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Started by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer` as a task of the gate's group, and run inline by the terms renewal after the
    Warden it restarted has dialled in. Calls into `hivemind.queen.swarm_gate.colonize` (its own
    steps), `hivemind.queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - A device is never left recorded as a Nuc with no server and nothing in motion to change
      that: the flow either proves the server or lowers the rung, or the Warden is unreachable
      and the device stays unproven for the next attempt.
    - Every wait on the Warden is bounded by the order's deadline plus a margin.
    - The order names only a server the Hive allows and models the Queen let the Warden load.
    - Lowering is the same move the operator's demotion makes (`lower_to_colonized`, `tidy_nuc`),
      so a lowered device is exactly a demoted one.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the restart of a Nuc.
    - hivemind.queen.swarm_gate.colonize.nuc.promote for the way up, whose order this repeats.
    - hivemind.wardens.ticks.nuc.run for the Warden's side, where a repeated order is idempotent.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.colonize.nuc.demote import lower_to_colonized, tidy_nuc
from hivemind.queen.swarm_gate.colonize.nuc.facts import RestoreOrder, restore_order
from hivemind.queen.swarm_gate.colonize.nuc.promote import (
    DEMOTE_DEADLINE_S,
    PROMOTE_DEADLINE_S,
    write_nuc_plan,
)
from hivemind.queen.trail import record_event
from hivemind.swarm import SwarmNode
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote, NucPromoted

# The words that mean the Warden could not be asked: nothing was decided, so nothing is lowered.
_UNREACHED = frozenset({"link_lost", "warden_silent"})

log = get_logger(__name__)

__all__ = ["NucRestoration"]


class NucRestoration:
    """Bring one Nuc's model server back, or lower the device to the colonized rung."""

    def __init__(self, env: FlowEnv, node: SwarmNode, warden: WardenLink) -> None:
        """Build the flow; `run` carries it out.

        Args:
            env: The Queen, the registry, the listener, the desk and the gate's numbers.
            node: The device, recorded on the Nuc rung, as the registry holds it.
            warden: The Queen's link to the device's own Warden, which has just connected.
        """
        self._env = env
        self._node = node
        self._warden = warden
        self._reason = "Nuc restoration: the Warden restarted, so its model server starts again."

    async def run(self) -> None:
        """Restore the server or lower the device; see `restore`."""
        await self.restore()

    async def restore(self) -> bool:
        """Start the server again, or make the registry say the device is a colonized one.

        Returns:
            True when the server answered and the device is a Nuc again; False when the device was
            lowered, or when the Warden could not be reached and nothing was decided.
        """
        env, node = self._env, self._node
        ceilings = env.context.deps.ledger.decisions.ceilings_for(node.warden_id)
        order = restore_order(ceilings, env.terms, env.desk.declared(node.device_id))
        if order is None:
            await self._lower("nothing_to_restore")
            return False
        try:
            reply = await self._start(order)
        except ColonizeStepError as error:
            if error.word in _UNREACHED:
                log.warning("swarm.nuc_restore_unreached", device_id=node.device_id)
            else:
                await self._lower(error.word)
            return False
        env.desk.prove(node.device_id)
        await write_nuc_plan(env, self._warden, reply)
        return True

    async def _start(self, order: RestoreOrder) -> NucPromoted:
        """Order the Warden to start the server; raise unless it reports PROMOTED."""
        node = self._node
        promote = NucPromote(
            cell_id=node.cell_id,
            device_id=node.device_id,
            action=NucAction.PROMOTE,
            server_id=order.server_id,
            initial_models=order.initial,
            deadline_s=PROMOTE_DEADLINE_S,
            reason=self._reason,
        )
        reply = await self._env.desk.ask(self._warden, promote, self._env.context.deps.clock)
        if reply.outcome is not NucOutcome.PROMOTED:
            # The Warden's own words are not recorded: a device may write what it likes there.
            raise ColonizeStepError("promote_failed")
        return reply

    async def _lower(self, word: str) -> None:
        """Stop anything the Warden started, lower the rung, tidy the Hive and say why."""
        env, node = self._env, self._node
        await self._stop_whatever_runs()
        reason = f"Nuc restoration failed ({word}): the device is a colonized one."
        lowered = await lower_to_colonized(env, node, reason)
        await tidy_nuc(env, lowered, self._warden)
        await record_event(
            env.context.deps,
            "queen.decided",
            node.device_id,
            reason="nuc_not_restored",
            detail=word,
        )

    async def _stop_whatever_runs(self) -> None:
        """Ask the Warden to stop any server it has; one that cannot answer is left."""
        node = self._node
        demote = NucPromote(
            cell_id=node.cell_id,
            device_id=node.device_id,
            action=NucAction.DEMOTE,
            server_id=None,
            initial_models=(),
            deadline_s=DEMOTE_DEADLINE_S,
            reason=self._reason,
        )
        try:
            await self._env.desk.ask(self._warden, demote, self._env.context.deps.clock)
        except ColonizeStepError as error:
            log.warning("swarm.nuc_demote_failed", device_id=node.device_id, word=error.word)
