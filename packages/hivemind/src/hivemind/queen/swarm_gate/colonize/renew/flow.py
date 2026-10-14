"""Define Renewal: restart a colonized device's Warden so it runs under the terms the operator set.

The operator changes a device's access level or Comb Shield tier with `hive swarm access` or
`shield`, and it must reach the device promptly, a narrowing above all. A device whose Warden runs
on it is told in two parts, because the two things on it hold the terms differently. The packet
persists the terms it is sent and its own checks apply them from the next request, so it is sent
`swarm.node_update` at once. The Warden holds the terms it was started with in its environment and
has no message that changes them, and the commands it runs go through a lease of its own that the
packet's checks do not constrain, so the only way for the Warden to run under the new terms is to
be started again. The lightest mechanism the pieces allow is the one the packet already has: stop
the Warden it started (WITHDRAW) and start it again (START), which builds the Warden's environment
from the terms the packet now holds. A withdrawal and a colonization would move the Warden to the
Hive Stand and back, and a Warden that took terms itself would need a message and a lease change
that do not exist.

The Queen does it in a fixed order, each step bounded, and never waits for the device to go idle:

1. The packet is sent the terms. If they cannot be sent nothing has changed and the next pass tries
   again, because a Warden started now would be started under the old ones.
2. Every running task of the Warden is paused into a Handoff and the Cell is held, and the work
   that had no bee (assigned or blocked) is taken back to be placed afresh.
3. The packet stops the Warden and starts it; the Queen waits for it to dial in under the same
   Warden id, and checks that its `cell.ready` names the new terms.
4. A Nuc's model server is started again (`NucRestoration`), so a Nuc stays a Nuc, or is lowered
   cleanly if it cannot be.
5. The paused tasks resume on the new Warden, each from its Handoff.

If any step fails after the tasks were paused the device falls back to Level 0 (`.fallback`), where
the Queen can always enforce the registry's terms, and the tasks resume there. A renewal cancelled
by the gate stopping takes its paused tasks back to PENDING rather than leave them PAUSED.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.renew`. Started by `hivemind.queen.swarm_gate.colonize.
    flow.Colonizer` as a task of the gate's group. Calls into `hivemind.cell`, `hivemind.queen.
    swarm_gate` (its own modules), `hivemind.swarm` and waggle only.

Key invariants:
    - Nothing is paused, withdrawn or started before the packet has been told the new terms.
    - Nothing waits for the device to go idle: the tasks are checkpointed and resumed instead.
    - Every wait on the device is bounded: the order deadlines, and `ATTACH_TIMEOUT_S` for the
      Warden's arrival.
    - A task is never failed by a renewal: it resumes, resumes on the Hive Stand after a fallback,
      or goes back to PENDING with the reason on the trail.
    - The device's rung does not change when the renewal works: a Nuc stays a Nuc, and the Warden
      keeps its id, its ceilings and its grants.
    - The flow does not raise for a step that failed: it falls back, and answers nothing to clear,
      since no operator request waits on it.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the choice of restart.
    - hivemind.queen.swarm_gate.colonize.renew.drift for when a renewal is due.
    - hivemind.queen.swarm_gate.colonize.promote for the move this one is built from.
"""

from __future__ import annotations

import asyncio
import sqlite3

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError, word_for
from hivemind.queen.swarm_gate.colonize.nuc.restore import NucRestoration
from hivemind.queen.swarm_gate.colonize.renew.drift import terms_drifted
from hivemind.queen.swarm_gate.colonize.renew.fallback import fall_back_to_level0
from hivemind.queen.swarm_gate.colonize.steps import (
    ATTACH_TIMEOUT_S,
    MOVING_REASON,
    START_DEADLINE_S,
    WITHDRAW_DEADLINE_S,
    Move,
    StepOrder,
    pause_for_move,
    resume_all,
    send_order,
    take_back_the_rest,
    tell_terms,
)
from hivemind.queen.swarm_gate.takeback import take_back
from hivemind.swarm import DeviceLink, RuntimeLevel, SwarmError, SwarmNode
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome

# What the packet is told, and so what the operator sees if the device's own log is read.
TERMS_CHANGED_REASON = "The operator changed this device's access level or Comb Shield tier."

log = get_logger(__name__)

__all__ = ["TERMS_CHANGED_REASON", "Renewal"]


class Renewal:
    """Restart one colonized device's Warden under its present terms, falling back if it fails."""

    def __init__(self, env: FlowEnv, node: SwarmNode, link: DeviceLink) -> None:
        """Build the flow; `run` carries it out.

        Args:
            env: The Queen, the Level 0 Wardens, the registry, the listener and the desk.
            node: The device, on the second or third rung, as the registry holds it now.
            link: The device's open packet link.
        """
        self._env = env
        self._move = Move(node, link)
        self._warden: WardenLink | None = None  # The new Warden's link, once it has dialled in.
        self._reason = "Terms renewal: the Warden restarts under the terms the operator set."

    async def run(self) -> None:
        """Carry out the renewal; a failed step falls back, a cancellation takes the tasks back."""
        env, move = self._env, self._move
        # First, and alone: a Warden is started from what the packet holds, so it must hold these.
        # (A pass may have told it already; the hub's record says so and nothing is sent twice.)
        if not await tell_terms(env.listener.hub, move.link, move.node, TERMS_CHANGED_REASON):
            log.warning("swarm.renew_terms_not_sent", device_id=move.node.device_id)
            return  # Nothing has changed; the next pass finds the difference again.
        try:
            await pause_for_move(env.context, move, self._reason)
            await take_back_the_rest(env.context, move, "renewing")
            await self._restart()
            await self._settle()
        except asyncio.CancelledError:
            await self._abandon()  # The gate is stopping: leave nothing PAUSED.
            raise
        except (SwarmError, CellError, sqlite3.Error) as error:
            await fall_back_to_level0(env, move, word_for(error))
        finally:
            # However it ended, the Cell is open to the dispatcher again (a no-op once resumed).
            env.context.deps.dispatch.held.release(move.node.cell_id)

    async def _restart(self) -> None:
        """Stop the device's Warden, start it again, and wait for it to dial in under new terms."""
        env, move = self._env, self._move
        node = move.node
        # Built before anything is stopped: an address the listener cannot give is found out here.
        start = StepOrder(
            ColonizeAction.START, START_DEADLINE_S, self._reason, warden_url=env.listener.warden_url
        )
        stop = StepOrder(ColonizeAction.WITHDRAW, WITHDRAW_DEADLINE_S, self._reason)
        reply = await send_order(move.link, node, stop)
        if reply.outcome is not ColonizeOutcome.WITHDRAWN:
            raise ColonizeStepError("withdraw_failed")
        move.moved = True
        env.arrivals.expect(node.device_id)  # Before START: a quick Warden must not be turned away.
        reply = await send_order(move.link, node, start)
        if reply.outcome is not ColonizeOutcome.STARTED:
            raise ColonizeStepError("start_failed")
        self._warden = await env.arrivals.wait(node.device_id, ATTACH_TIMEOUT_S)
        # Its attach was a reconnection, which opened the Cell; nothing is placed until it resumes.
        env.context.deps.dispatch.held.hold(node.cell_id, MOVING_REASON)
        if terms_drifted(env.desk.reported(node.device_id), node):
            # The packet started it under terms other than these: it was never told, or told wrong.
            raise ColonizeStepError("terms_not_applied")

    async def _settle(self) -> None:
        """Start a Nuc's server again, then resume the paused tasks on the new Warden."""
        env, move = self._env, self._move
        deps = env.context.deps
        if move.node.level is RuntimeLevel.NUC and self._warden is not None:
            await NucRestoration(env, move.node, self._warden).restore()
        stranded = await resume_all(env.context, move, "The Warden restarted under the new terms.")
        if stranded:
            await take_back(deps, move.node, "resume_failed", only=stranded)

    async def _abandon(self) -> None:
        """Take the tasks this flow paused back to PENDING; the gate is stopping."""
        move = self._move
        if move.paused:
            await take_back(self._env.context.deps, move.node, "gate_stopping", only=move.paused)
