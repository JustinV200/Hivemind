"""Define Promotion: move a device's Warden onto the device (Level 0 to Level 1), or undo the move.

At Level 0 a device's Warden runs on the Hive Stand and reaches the device through its packet;
colonizing moves that Warden onto the device, under the same `warden_id`, so its sub-bees keep
working when the link to the Hive Stand drops (ADR-0052: the Warden always moves first). The Queen
does it in a fixed order, each step bounded, and any step that fails undoes the ones before it:

1. PREPARE: the packet installs the Hive's own runtime version beside itself.
2. Pause the Level 0 Warden's running tasks through Clustering's pause path, so every bee writes a
   Handoff, and take back whatever else sat on the Cell (assigned or blocked work), to be placed
   afresh.
3. Stop the Level 0 Warden, which releases its lease: the device is left as found.
4. START: the packet launches the device's Warden, told the swarm listener's address; wait,
   bounded, for it to dial in. Its attach is a reconnection of the same Warden id, so the ceilings,
   hosting plan and live grants the Queen holds for it are sent again by the attach path itself.
5. Commit: move the rung (`swarm.promoted`, written with the change), record `warden.migrated`
   (the Hive Stand's node to the device's), tell the packet its new rung, and resume each paused
   task on the device's Warden from its Handoff, carried inline because that Warden cannot read
   the Queen's store.

If any step fails the Queen sends WITHDRAW, lets go of any Warden that did dial in, puts the rung
back if it had moved (`swarm.demoted`, with the failure as a stable word), rebuilds the Level 0
Warden and resumes the tasks there. A flow cancelled by the gate stopping takes its paused tasks
back to PENDING rather than leave them PAUSED. The operator's request is answered either way and
cleared, so a failed colonization is not retried on every pass.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Started by `.flow.Colonizer` as a task of the gate's
    group. Calls into `hivemind.brood_chamber`, `hivemind.cell`, `hivemind.queen.swarm_gate`
    (its own modules), `hivemind.queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - Every wait on the device is bounded: PREPARE by its install deadline, START and WITHDRAW by
      theirs, the Warden's arrival by `ATTACH_TIMEOUT_S`.
    - Nothing is sent to the device before the Guard has said yes (`Colonizer.request`).
    - A task is never failed by a colonization: it is resumed, resumed on the Hive Stand after an
      undo, or taken back to PENDING with the reason on the trail.
    - The registry's rung moves only after the device's Warden has attached, so a Warden that never
      arrives leaves the device a Level 0 one.
    - Undoing never raises past the flow for a device that is gone: it records what it could.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the move and its rollback.
    - hivemind.queen.swarm_gate.colonize.withdraw for the way back down.
    - hivemind.queen.dispatcher.ready.handoff for how a resumed task carries its Handoff.
"""

from __future__ import annotations

import asyncio
import sqlite3

from hivemind.common.logging import get_logger
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError, word_for
from hivemind.queen.swarm_gate.colonize.events import (
    level_event,
    record_migrated,
    undone_event,
)
from hivemind.queen.swarm_gate.colonize.steps import (
    ATTACH_TIMEOUT_S,
    DEAD_LINK_WORDS,
    MOVING_REASON,
    PREPARE_DEADLINE_S,
    START_DEADLINE_S,
    WITHDRAW_DEADLINE_S,
    Move,
    StepOrder,
    dismiss_remote,
    pause_for_move,
    resume_all,
    send_order,
    take_back_the_rest,
    tell_device,
)
from hivemind.queen.swarm_gate.errors import RECOVERABLE_ERRORS
from hivemind.queen.swarm_gate.takeback import take_back
from hivemind.queen.trail import record_event
from hivemind.swarm import DeviceLink, NodeStatus, RuntimeLevel, SwarmError, SwarmNode
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome

log = get_logger(__name__)

__all__ = ["Promotion"]


class Promotion:
    """Move one device's Warden onto it, undoing the move if any step of it fails."""

    def __init__(self, env: FlowEnv, node: SwarmNode, link: DeviceLink, why: str) -> None:
        """Build the flow; `run` carries it out.

        Args:
            env: The Queen, the Level 0 Wardens, the registry and the listener.
            node: The device, on the first rung, as the registry holds it.
            link: The device's open packet link.
            why: The reason the Queen decided to colonize it (`ColonizeReason`'s word).
        """
        self._env = env
        self._move = Move(node, link)
        self._reason = f"Colonization ({why}): the Warden moves onto its device."

    async def run(self) -> None:
        """Carry out the move; on any failure undo it, and on cancellation take the tasks back."""
        try:
            await self._prepare()
            await pause_for_move(self._env.context, self._move, self._reason)
            await self._hand_over()
            await self._commit()
        except asyncio.CancelledError:
            await self._abandon()  # The gate is stopping: leave nothing PAUSED.
            raise
        except RECOVERABLE_ERRORS as error:
            await self._undo(word_for(error))
        finally:
            # However it ended, the Cell is open to the dispatcher again (a no-op once resumed).
            self._env.context.deps.dispatch.held.release(self._move.node.cell_id)

    async def _prepare(self) -> None:
        """Have the packet install the Hive's runtime version; raise unless it says PREPARED."""
        move, runtime = self._move, self._env.terms.runtime_version
        step = StepOrder(
            ColonizeAction.PREPARE, PREPARE_DEADLINE_S, self._reason, runtime_version=runtime
        )
        reply = await send_order(move.link, move.node, step)
        if reply.outcome is not ColonizeOutcome.PREPARED:
            raise ColonizeStepError("prepare_failed")

    async def _hand_over(self) -> None:
        """Stop the Level 0 Warden, start the device's, and wait for it to attach."""
        env, move = self._env, self._move
        node = move.node
        # Built before anything is stopped: an address the listener cannot give is found out here.
        step = StepOrder(
            ColonizeAction.START,
            START_DEADLINE_S,
            self._reason,
            warden_url=env.listener.warden_url,
        )
        await env.wardens.stop(node.device_id)  # Releases its lease: the device is left as found.
        move.moved = True
        await take_back_the_rest(env.context, move, "colonizing")
        env.arrivals.expect(node.device_id)  # Before START: a quick Warden must not be turned away.
        reply = await send_order(move.link, node, step)
        if reply.outcome is not ColonizeOutcome.STARTED:
            raise ColonizeStepError("start_failed")
        await env.arrivals.wait(node.device_id, ATTACH_TIMEOUT_S)
        # Held again, as the renewal does: the Cell stays closed to the dispatcher until the move
        # is committed and `resume_all` opens it. The flow's own guarantee, whatever the attach
        # path does with a returning Warden's Cell, so nothing is placed on a Warden the undo may
        # dismiss a moment later.
        env.context.deps.dispatch.held.hold(node.cell_id, MOVING_REASON)

    async def _commit(self) -> None:
        """Move the rung, record the migration, tell the device, and resume the paused tasks."""
        env, move = self._env, self._move
        deps = env.context.deps
        event = level_event(env.listener.recorder, move.node, RuntimeLevel.COLONIZED, self._reason)
        try:
            updated = await env.registry.set_level(
                move.node.device_id, RuntimeLevel.COLONIZED, event
            )
        except (SwarmError, sqlite3.Error) as error:
            raise ColonizeStepError("level_not_set") from error
        move.committed = True
        env.listener.hub.update_node(updated)
        await record_migrated(deps, updated, deps.identity.node_id)
        await tell_device(move.link, updated, "The Warden moved onto this device.")
        stranded = await resume_all(env.context, move, "The Warden moved onto its device.")
        if stranded:
            await take_back(deps, updated, "resume_failed", only=stranded)

    async def _undo(self, word: str) -> None:
        """Roll the move back: WITHDRAW, let go of the device's Warden, restore what was changed."""
        env, move = self._env, self._move
        log.warning("swarm.colonize_undone", device_id=move.node.device_id, word=word)
        if word not in DEAD_LINK_WORDS:
            await self._withdraw()
        if move.moved:
            await dismiss_remote(env.context, env.wardens, move.node)
            # Whatever reached the Cell since the pause (a placement that slipped in while the
            # device's Warden was attached) has no bee now that Warden is dismissed: taken back
            # to be placed afresh, the paused tasks left for `_restore` to resume.
            await take_back_the_rest(env.context, move, "undone")
        node = await env.registry.node(move.node.device_id)
        if node is None or node.status is NodeStatus.REVOKED:
            # Nothing to rebuild for a device the operator removed meanwhile; its tasks move on.
            await take_back(env.context.deps, move.node, "revoked_during_move", only=move.paused)
            return
        node = await self._record_undone(node, word)
        await self._restore(node, word)

    async def _withdraw(self) -> None:
        """Ask the packet to stop any Warden it started; a packet that cannot answer is left."""
        move = self._move
        step = StepOrder(ColonizeAction.WITHDRAW, WITHDRAW_DEADLINE_S, self._reason)
        try:
            await send_order(move.link, move.node, step)
        except ColonizeStepError as error:
            log.warning(
                "swarm.colonize_withdraw_failed", device_id=move.node.device_id, word=error.word
            )

    async def _record_undone(self, node: SwarmNode, word: str) -> SwarmNode:
        """Put the rung back if it moved, record how the colonization failed, clear the request."""
        env, move = self._env, self._move
        recorder = env.listener.recorder
        event = undone_event(recorder, move.node, word)
        if move.committed:
            node = await env.registry.set_level(node.device_id, RuntimeLevel.GATEWAY, event)
            env.listener.hub.update_node(node)
        elif move.moved:
            await recorder.record(event)  # The Warden left the Hive Stand: that is a demotion.
        else:
            deps = env.context.deps
            await record_event(
                deps, "queen.decided", node.device_id, reason="colonize_failed", detail=word
            )
        # Answered: the operator's request is cleared, so it is not tried again on every pass.
        return await env.registry.set_desired_level(node.device_id, None)

    async def _restore(self, node: SwarmNode, word: str) -> None:
        """Rebuild the Level 0 Warden if it was stopped, and resume the paused tasks there."""
        env, move = self._env, self._move
        deps = env.context.deps
        if move.moved and not await env.wardens.serve(node):
            await take_back(deps, node, "level0_not_rebuilt", only=move.paused)
            return
        reason = f"The colonization was undone ({word}); the Warden is on the Hive Stand."
        stranded = await resume_all(env.context, move, reason)
        if stranded:
            await take_back(deps, node, "resume_failed", only=stranded)

    async def _abandon(self) -> None:
        """Take the tasks this flow paused back to PENDING; the gate is stopping."""
        move = self._move
        if move.paused:
            await take_back(self._env.context.deps, move.node, "gate_stopping", only=move.paused)
