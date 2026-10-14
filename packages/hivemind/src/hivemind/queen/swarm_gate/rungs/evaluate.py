"""Define RungEvaluator: the Queen's own rung decisions, taken from what she knows each pass.

The gate's reconcile pass hands this evaluator every reachable device that has no `desired_level`,
and the operator's word, when there is one, is carried out by the colonizer without it. For each
device it settles the move it launched before, reads the facts (`.readings`, once per pass),
asks `decide_rung` and acts on the verdict: it launches the step through the colonizer, which asks
the Guard like for every rung and runs the flow the operator's request would, with the Queen's
reason in the `swarm.promoted` or `swarm.demoted` it writes; it refreshes the mark of a rung whose
reason still holds; and it records, once, a reason that holds on a device that cannot carry the
rung (`queen.decided` with `rung_declined` and the stable word). A device the Guard will not let her
move is backed off like a flow that failed, so she does not ask it again on every pass.

Two things keep her from overshooting. A move made for the same reason is not launched while another
is still running: relieving the Hive Stand by one device changes the pressure, so the next device is
judged on the next pass and not on the figure the first move was meant to change. And the devices
are met heaviest first (`begin_pass`), so the one move that is made is the one that relieves the
Hive Stand most.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.rungs`. Built by `hivemind.queen.swarm_gate.gate.SwarmGate`, bound
    to the Queen when the gate starts, and called by `hivemind.queen.swarm_gate.reconcile`. Calls
    into `hivemind.queen.attach` (the Warden's reported state), `hivemind.queen.swarm_gate` (its
    own modules), `hivemind.queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - A device with a pending `desired_level` is never evaluated: the operator always wins.
    - Every move is launched through `Colonizer.launch`, so the Guard is asked before anything is
      sent to a device and the flows are the operator's own.
    - Nothing is launched on a device that a flow already owns.
    - At most one move for each reason is in flight at a time, and a refusal is recorded once.
    - The evaluation reads the Hive and the device and changes nothing but through a flow.
    - A Hive she cannot read this pass is one she moves nothing in; the rest of the pass goes on.

See Also:
    - hivemind.queen.swarm_gate.rungs.decide for the pure decision this acts on.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the Queen's own rung decisions.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from hivemind.brood_chamber import BroodChamberError
from hivemind.common.logging import get_logger
from hivemind.queen.attach import warden_state
from hivemind.queen.swarm_gate.colonize.flow import GUARD_DENIED_WORD, Colonizer
from hivemind.queen.swarm_gate.colonize.request import Ask, Step
from hivemind.queen.swarm_gate.colonize.steps import is_idle, live_node
from hivemind.queen.swarm_gate.rungs.decide import RungFacts, Verdict, decide_rung, target_rung
from hivemind.queen.swarm_gate.rungs.keeper import RungKeeper
from hivemind.queen.swarm_gate.rungs.readings import Readings, read_world
from hivemind.queen.swarm_gate.terms import GateContext, GateTerms
from hivemind.queen.trail import record_event
from hivemind.swarm import GatewayHub, RuntimeLevel, SwarmNode
from waggle.clock import Clock
from waggle.messages.supervision import WardenState
from waggle.messages.task import WorkerRole

log = get_logger(__name__)

# The reasons whose moves are made one at a time: each changes the figure the next is judged on.
_ONE_AT_A_TIME = frozenset({"relief", "pressure"})
# The steps that take a Warden, or its model server, off a device: only while it holds no work.
_LOWERING = frozenset({Step.WITHDRAW, Step.NUC_DEMOTE})

__all__ = ["RungEvaluator"]


class RungEvaluator:
    """Decide, from what the Queen knows, which devices to raise or take back a rung."""

    def __init__(
        self, colonizer: Colonizer, hub: GatewayHub, clock: Clock, terms: GateTerms
    ) -> None:
        """Build the evaluator; `bind` gives it the Queen.

        Args:
            colonizer: Launches the flows and says which devices a flow owns.
            hub: Holds each connected device's latest capacity.
            clock: The Queen's clock; marks and back-offs are read against it.
            terms: The gate's numbers.
        """
        self._colonizer = colonizer
        self._hub = hub
        self._clock = clock
        self._terms = terms
        self._keeper = RungKeeper(terms)
        self._context: GateContext | None = None
        self._readings: Readings | None = None

    def bind(self, context: GateContext) -> None:
        """Work against `context`'s Queen from now on."""
        self._context = context

    async def begin_pass(self, nodes: Sequence[SwarmNode]) -> tuple[SwarmNode, ...]:
        """Start a pass: settle the moves launched before and order the devices heaviest first.

        Args:
            nodes: Every enrolled device, as the registry holds it.

        Returns:
            The same devices, the one whose sub-bees load the Hive Stand most first, so the move
            a pressure allows is made on the device that relieves it most.
        """
        context, now = self._context, self._clock.now()
        self._readings = None  # Until this pass has read the Hive: no stale figure judges a device.
        for node in nodes:
            if not self._colonizer.busy(node.device_id):
                self._keeper.settle(node, now)
        if context is None or not nodes:
            return tuple(nodes)
        try:
            readings = self._readings = await read_world(context, nodes)
        except (BroodChamberError, sqlite3.Error) as error:
            # Her own decisions are an improvement on the Hive, not its upkeep: a Hive she cannot
            # read is one she moves no device in, and the pass goes on to the work the operator
            # is waiting on (a revoked device, a lost one's hold). The type only: no row is logged.
            log.error("swarm.rungs_unread", error=type(error).__name__)
            return tuple(nodes)
        return tuple(sorted(nodes, key=lambda node: -readings.share_of(node)))

    async def evaluate(self, node: SwarmNode) -> bool:
        """Raise or take back a rung of `node` if she decides to; True when a flow now owns it.

        Args:
            node: A reachable device with no pending request, as the registry holds it.

        Returns:
            True when a flow owns the device (one was already running, or this call started one).
        """
        context, readings = self._context, self._readings
        if context is None or readings is None or node.desired_level is not None:
            return False
        if self._colonizer.busy(node.device_id):
            return True
        facts = self._facts(context, readings, node)
        return await self._act(node, decide_rung(facts))

    def _facts(self, context: GateContext, readings: Readings, node: SwarmNode) -> RungFacts:
        """Gather what `decide_rung` weighs about `node` from the pass's readings."""
        colonized = node.level is not RuntimeLevel.GATEWAY
        watching = warden_state(context.queen, node.warden_id) is WardenState.WATCH
        return RungFacts(
            node=live_node(self._hub, node),
            terms=self._terms,
            footprint=context.deps.footprints[WorkerRole.DRONE],
            now=self._clock.now(),
            relief=readings.relief_for(node),
            seats=readings.seats_for(node),
            survives=node.cell_id in readings.survival,
            declared=self._colonizer.declared(node.device_id),
            idle=colonized and watching and node.cell_id not in readings.busy,
            memory=self._keeper.memory(node.device_id),
        )

    async def _act(self, node: SwarmNode, verdict: Verdict) -> bool:
        """Carry out a verdict: refresh a mark, record a refusal once, launch a step."""
        keeper, now = self._keeper, self._clock.now()
        if verdict.seen is not None:
            keeper.mark_seen(node.device_id, verdict.seen, now)
        refusal = verdict.declined
        if keeper.declined(node.device_id, refusal) and refusal is not None:
            await self._record_declined(node, refusal)
        ask = verdict.ask
        if ask is None or ask.step is None or not await self._may_launch(node, ask):
            return False
        move = (node.level, target_rung(ask.step))
        word = await self._colonizer.launch(node, ask)
        if word is None:
            keeper.begin(node.device_id, move, ask.word)
        elif word == GUARD_DENIED_WORD:
            keeper.failed(node.device_id, move, now)  # The Guard said no: not asked every pass.
        return word is None

    async def _may_launch(self, node: SwarmNode, ask: Ask) -> bool:
        """Whether a step decided on this pass's readings may still be launched now."""
        if ask.word in _ONE_AT_A_TIME and self._keeper.pending(ask.word):
            return False  # A move for this reason is still changing the figure it was judged on.
        if ask.step not in _LOWERING or self._context is None:
            return True
        # Work may have been placed on the device since the pass read the Hive, and the flow that
        # takes a Warden off a device does not look again, so she looks once more before it does.
        return await is_idle(self._context, node)

    async def _record_declined(self, node: SwarmNode, word: str) -> None:
        """Record on the trail that a reason holds for a device that cannot carry the rung."""
        context = self._context
        if context is not None:
            await record_event(
                context.deps,
                "queen.decided",
                node.device_id,
                reason="rung_declined",
                detail=word,
            )
