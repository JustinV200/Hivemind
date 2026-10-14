"""Define NucPromotion: give a colonized device a model server of its own (Level 1 to Level 2).

A colonized device's Warden already runs on it, so what makes it a Nuc is a model server beside the
Warden, which keeps its sub-bees thinking when the link to the Hive Stand is lost (ADR-0052). The
Queen decides (`nuc_decision`, pure), asks the Guard, and then does it in a fixed order, each step
bounded, undoing what it did if any step fails:

1. Set the Warden's ceilings (`forage.ceilings_set`): the VRAM and disk it may fill, and the models
   it may load by itself (codingrules 8.10, "ceilings, not approvals"). They go first, so the
   Warden is never asked to start a server it has no bounds for, and it fails closed without them.
2. Send `swarm.nuc_promote(PROMOTE)` naming the server and the models to load first, and wait,
   bounded, for the Warden's `swarm.nuc_promoted`. Its model sources reach the Queen as
   `forage.source_added` frames of their own.
3. Commit: move the rung (`swarm.promoted`, written with the change), update the hub's record and
   tell the device's packet.
4. Mark the device proven (its server answered) and write its hosting plan with its own models
   first and the shared pool as the fallback (`write_nuc_plan`, which a restoration shares),
   counting the models the reply says are loaded as local to the device without waiting for the
   frames that announce them.

If a step fails after the order went out the Queen sends DEMOTE (unless the link is known dead),
puts the rung back if it had moved, restores the ceilings it replaced, and records the failure as
a stable word; the operator's request is answered either way and cleared. A promotion cancelled by
the gate stopping does the same.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Started by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer` as a task of the gate's group. Calls into `hivemind.cell`, `hivemind.queen.forage`
    (ceilings, hosting plan), `hivemind.queen.swarm_gate.colonize` (its own steps), `hivemind.
    queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - The Warden is never asked to start a server before its ceilings are set.
    - The registry's rung moves only after the Warden reported PROMOTED, so a Warden that never
      answers leaves the device a colonized one.
    - Every wait on the Warden is bounded by the order's deadline plus a margin.
    - Whatever the Queen changed (ceilings, rung) is put back if the promotion fails.
    - A failure to write the hosting plan after the rung moved is logged, not undone: the device
      is a Nuc, and the plan is written again at the next change.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder.
    - hivemind.queen.swarm_gate.colonize.nuc.demote for the way back down.
    - hivemind.wardens.ticks.nuc for the Warden's side of the order.
"""

from __future__ import annotations

import asyncio
import sqlite3

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.forage import ModelSource
from hivemind.queen.deps import WardenLink
from hivemind.queen.forage import (
    change_ceilings,
    initial_ceilings,
    set_ceilings,
    write_hosting_plan,
)
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError, word_for
from hivemind.queen.swarm_gate.colonize.events import level_event
from hivemind.queen.swarm_gate.colonize.nuc.facts import nuc_ceilings
from hivemind.queen.swarm_gate.colonize.steps import tell_device
from hivemind.queen.trail import record_event
from hivemind.swarm import NucDecision, RuntimeLevel, SwarmError, SwarmNode
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote, NucPromoted

# How long the Warden may take to bring the server up: it loads model weights from disk and must
# pass its health probe, which is minutes at most. Its own manifest's start-up bound applies too.
PROMOTE_DEADLINE_S = 180.0
DEMOTE_DEADLINE_S = 60.0  # Stopping a process and withdrawing its sources takes seconds.
# The words that mean the Warden cannot be asked anything: sending it DEMOTE would only wait out
# another deadline.
_DEAD_WORDS = frozenset({"link_lost", "warden_silent"})

log = get_logger(__name__)

__all__ = ["DEMOTE_DEADLINE_S", "PROMOTE_DEADLINE_S", "NucPromotion", "write_nuc_plan"]


async def write_nuc_plan(env: FlowEnv, warden: WardenLink, reply: NucPromoted) -> None:
    """Write the device's hosting plan: its own models first, the shared pool behind them.

    The models the reply says are loaded are counted as local to the device without waiting for
    the frames that announce them. A failure is logged, not raised: the device is a Nuc already,
    and the plan is written again at the next change.

    Args:
        env: The Queen, the gate's numbers (the servers the Hive allows) and the registry.
        warden: The Queen's link to the device's own Warden, which the plan is sent to.
        reply: The Warden's `swarm.nuc_promoted`: the server and the models it loaded.
    """
    server = next((s for s in env.terms.servers if s.server_id == reply.server_id), None)
    loaded: tuple[ModelSource, ...] = ()
    if server is not None:
        loaded = tuple(s for s in server.sources if s.source_id in reply.loaded_models)
    try:
        await write_hosting_plan(warden.cell, env.context.deps, warden, local=loaded)
    except (SwarmError, sqlite3.Error) as error:
        log.error("swarm.nuc_plan_failed", device_id=reply.device_id, error=type(error).__name__)


class NucPromotion:
    """Start a model server on one colonized device, undoing what was done if any step fails."""

    def __init__(
        self, env: FlowEnv, node: SwarmNode, warden: WardenLink, decision: NucDecision
    ) -> None:
        """Build the flow; `run` carries it out.

        Args:
            env: The Queen, the registry, the listener, the desk and the gate's numbers.
            node: The device, on the second rung, with the capacity the decision was made on.
            warden: The Queen's link to the device's own Warden.
            decision: The accepted promotion: the server, its models and why.
        """
        self._env = env
        self._node = node
        self._warden = warden
        self._decision = decision
        why = decision.reason.value if decision.reason is not None else "operator"
        self._reason = f"Nuc promotion ({why}): the device starts a model server of its own."
        # What the Warden is bound by now, to put back if the promotion fails.
        self._previous = env.context.deps.ledger.decisions.ceilings_for(node.warden_id)
        self._ceilings_set = False
        self._ordered = False
        self._lifted: SwarmNode | None = None  # The device on the Nuc rung, once the rung moved.

    async def run(self) -> None:
        """Carry out the promotion; on any failure undo it, and on cancellation undo it too."""
        try:
            await self._set_ceilings()
            reply = await self._order()
            await self._commit()
        except asyncio.CancelledError:
            await self._undo("gate_stopping")
            raise
        except (SwarmError, CellError, sqlite3.Error) as error:
            await self._undo(word_for(error))
            return
        # The server answered: until this Warden connects again there is nothing to start afresh.
        self._env.desk.prove(self._node.device_id)
        await write_nuc_plan(self._env, self._warden, reply)

    async def _set_ceilings(self) -> None:
        """Set the ceilings a model server needs, before the Warden is asked to start one."""
        base = self._previous or initial_ceilings(self._warden.cell)
        ceilings = nuc_ceilings(base, self._node, self._decision)
        deps = self._env.context.deps
        if self._previous is None:
            await set_ceilings(self._warden, ceilings, deps)
        else:
            await change_ceilings(self._warden, ceilings, deps)
        self._ceilings_set = True

    async def _order(self) -> NucPromoted:
        """Order the Warden to start the server; raise unless it reports PROMOTED."""
        node, decision = self._node, self._decision
        order = NucPromote(
            cell_id=node.cell_id,
            device_id=node.device_id,
            action=NucAction.PROMOTE,
            server_id=decision.server_id,
            initial_models=decision.initial,
            deadline_s=PROMOTE_DEADLINE_S,
            reason=self._reason,
        )
        self._ordered = True  # From here the Warden may be running a server the Queen must end.
        reply = await self._env.desk.ask(self._warden, order, self._env.context.deps.clock)
        if reply.outcome is not NucOutcome.PROMOTED:
            # The Warden's own words are not recorded: a device may write what it likes there.
            raise ColonizeStepError("promote_failed")
        return reply

    async def _commit(self) -> None:
        """Move the rung, update the hub's record and tell the device's packet."""
        env, node = self._env, self._node
        event = level_event(env.listener.recorder, node, RuntimeLevel.NUC, self._reason)
        try:
            updated = await env.registry.set_level(node.device_id, RuntimeLevel.NUC, event)
        except (SwarmError, sqlite3.Error) as error:
            raise ColonizeStepError("level_not_set") from error
        self._lifted = updated
        env.listener.hub.update_node(updated)
        link = env.listener.hub.link(node.device_id)
        if link is not None:
            await tell_device(link, updated, "The device became a Nuc.")

    async def _undo(self, word: str) -> None:
        """Roll back: end any server, lower the rung, restore the ceilings, answer the request."""
        env, node = self._env, self._node
        log.warning("swarm.nuc_undone", device_id=node.device_id, word=word)
        if self._ordered and word not in _DEAD_WORDS:
            await self._demote()
        if self._lifted is not None:
            event = level_event(env.listener.recorder, self._lifted, RuntimeLevel.COLONIZED, word)
            lowered = await env.registry.set_level(node.device_id, RuntimeLevel.COLONIZED, event)
            env.listener.hub.update_node(lowered)
        if self._ceilings_set:
            back = self._previous or initial_ceilings(self._warden.cell)
            await change_ceilings(self._warden, back, env.context.deps)
        deps = env.context.deps
        await record_event(deps, "queen.decided", node.device_id, reason="nuc_failed", detail=word)
        # Answered: the operator's request is cleared, so it is not tried again on every pass.
        await env.registry.set_desired_level(node.device_id, None)

    async def _demote(self) -> None:
        """Ask the Warden to stop any server it started; one that cannot answer is left."""
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
        try:
            await self._env.desk.ask(self._warden, order, self._env.context.deps.clock)
        except ColonizeStepError as error:
            log.warning("swarm.nuc_demote_failed", device_id=node.device_id, word=error.word)
