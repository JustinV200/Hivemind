"""Define Colonizer: carry out the rung an operator asked for on a device, one flow at a time.

The gate's reconcile pass finds a device whose `desired_level` differs from its rung and asks the
`Colonizer` to carry the request out. The Colonizer decides (`.request.decide_request`, pure),
asks the Guard whether the Queen may change this device's rung (`.guard.authorise_rung`, for a
rung up and a rung down alike), and then starts the flow, `Promotion` or `Withdrawal`, as a task
of the gate's group: a colonization can spend minutes installing a runtime on the device, and the
gate's loop must go on serving every other device while it does. While a flow runs it owns the
device: the reconcile pass leaves the node alone (`busy`), and the gate's door admits the Warden
the flow started (`expects`) and reports its arrival (`arrived`).

A request is answered once. If it is refused (the device is too small, its Warden is busy, the
Guard said no, the rung is a Nuc's) the Queen records one `queen.decided` row with the reason's
word and clears the request, so a refusal costs one row and not one per pass. If a flow fails, the
flow itself undoes the move and clears the request; if something outside the flow's own handling
goes wrong, this class records it and clears the request too.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Built by `hivemind.queen.swarm_gate.gate.SwarmGate`,
    bound to the Queen when the gate starts, called by `hivemind.queen.swarm_gate.reconcile` and
    by the gate's door. Calls into `hivemind.queen.attach` (the Warden's reported state),
    `hivemind.queen.swarm_gate` (its own modules), `hivemind.queen.trail`, `hivemind.swarm` and
    waggle only.

Key invariants:
    - At most one flow runs per device, and a device with a flow running is skipped by the
      reconcile pass.
    - Nothing is sent to a device, paused or stopped before the Guard has said yes.
    - A withdrawal is ordered only of a Warden that reports `WATCH` and has no task on its Cell.
    - Every flow ends by waking the gate's loop, so the device's next state is looked at at once.
    - `stop` cancels every flow still running and waits for it; each cancelled promotion takes
      the tasks it paused back to PENDING.

See Also:
    - hivemind.queen.swarm_gate.colonize.request for the pure decision.
    - hivemind.queen.swarm_gate.colonize.promote and .withdraw for the flows.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from typing import Protocol

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.forage import RoleFootprint
from hivemind.queen.attach import warden_state
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize.arrivals import Arrivals
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import word_for
from hivemind.queen.swarm_gate.colonize.guard import authorise_rung
from hivemind.queen.swarm_gate.colonize.promote import Promotion
from hivemind.queen.swarm_gate.colonize.request import Step, decide_request
from hivemind.queen.swarm_gate.colonize.withdraw import Withdrawal
from hivemind.queen.swarm_gate.takeback import tasks_on
from hivemind.queen.swarm_gate.terms import GateContext, GateTerms
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.queen.trail import record_event
from hivemind.swarm import RuntimeLevel, SwarmError, SwarmListener, SwarmNode, SwarmRegistry
from waggle.ids import DeviceId
from waggle.messages.supervision import WardenState
from waggle.messages.task import WorkerRole

log = get_logger(__name__)

GUARD_DENIED_WORD = "guard_denied"  # The Guard did not let the Queen change the device's rung.

__all__ = ["GUARD_DENIED_WORD", "Colonizer", "Flow"]


class Flow(Protocol):
    """One rung change in progress: it runs to its end and raises only what it could not undo."""

    async def run(self) -> None:
        """Carry out the flow; see `Promotion.run` and `Withdrawal.run`."""
        ...


class Colonizer:
    """Decide, authorise and run the rung changes the operator asked for, one flow per device."""

    def __init__(
        self,
        wardens: DeviceWardens,
        registry: SwarmRegistry,
        listener: SwarmListener,
        terms: GateTerms,
        wake: Callable[[], None],
    ) -> None:
        """Build the Colonizer; `bind` gives it the Queen and `open` the group its flows run in.

        Args:
            wardens: The Level 0 Wardens, which a flow stops and rebuilds.
            registry: Where the rung and the pending request are kept.
            listener: The swarm listener whose hub reaches the devices.
            terms: The gate's numbers.
            wake: Wakes the gate's loop; called whenever a flow ends.
        """
        self._wardens = wardens
        self._registry = registry
        self._listener = listener
        self._terms = terms
        self._wake = wake
        self._arrivals = Arrivals()
        self._env: FlowEnv | None = None
        self._group: asyncio.TaskGroup | None = None
        self._flows: dict[DeviceId, asyncio.Task[None]] = {}

    def bind(self, context: GateContext) -> None:
        """Work against `context`'s Queen from now on."""
        self._env = FlowEnv(
            context, self._wardens, self._registry, self._listener, self._terms, self._arrivals
        )

    def open(self, group: asyncio.TaskGroup) -> None:
        """Run every flow started from now on as a task of `group`, the gate's own."""
        self._group = group

    def close(self) -> None:
        """Stop starting flows in a group that is over."""
        self._group = None

    def busy(self, device_id: DeviceId) -> bool:
        """Whether a flow owns `device_id` right now."""
        return device_id in self._flows

    def expects(self, device_id: DeviceId) -> bool:
        """Whether a flow is waiting for `device_id`'s Warden to dial in."""
        return self._arrivals.expects(device_id)

    def arrived(self, device_id: DeviceId, link: WardenLink) -> None:
        """Report that `device_id`'s Warden attached under `link`; wakes the flow waiting."""
        self._arrivals.arrived(device_id, link)

    async def request(self, node: SwarmNode) -> bool:
        """Carry out or refuse the rung `node` asks for; True when a flow now owns the device.

        Args:
            node: A reachable device whose `desired_level` differs from its rung.

        Returns:
            True when a flow was started; False when nothing was asked, the device has no live
            packet link yet (the request waits), the gate is not running, or the request was
            refused (the refusal is recorded and the request cleared).
        """
        env, group = self._env, self._group
        link = self._listener.hub.link(node.device_id)
        if env is None or group is None or link is None or node.desired_level is None:
            return False
        if self.busy(node.device_id):
            return True
        idle = await self._is_idle(env.context, node)
        ask = decide_request(node, self._terms, _footprint(env), is_idle=idle)
        if ask.step is None:
            await self._refuse(env, node, ask.word)
            return False
        if not await authorise_rung(env.context.deps, node):
            await self._refuse(env, node, GUARD_DENIED_WORD)
            return False
        flow: Flow = (
            Promotion(env, node, link, ask.word)
            if ask.step is Step.PROMOTE
            else Withdrawal(env, node, link, ask.word)
        )
        self._flows[node.device_id] = group.create_task(self._guarded(env, node, flow))
        return True

    async def stop(self) -> None:
        """Cancel every flow still running and wait for each to undo what it must."""
        flows = tuple(self._flows.values())
        for task in flows:
            task.cancel()
        await asyncio.gather(*flows, return_exceptions=True)

    async def _is_idle(self, context: GateContext, node: SwarmNode) -> bool:
        """Whether the device's own Warden reports WATCH and holds no task (a withdrawal's test)."""
        if node.level is not RuntimeLevel.COLONIZED:
            return False
        state = warden_state(context.queen, node.warden_id)
        return state is WardenState.WATCH and not await tasks_on(context.deps, node)

    async def _refuse(self, env: FlowEnv, node: SwarmNode, word: str) -> None:
        """Record why a request was refused, once, and clear it so it is not asked again."""
        wanted = node.desired_level.value if node.desired_level is not None else ""
        await record_event(
            env.context.deps,
            "queen.decided",
            node.device_id,
            reason="rung_refused",
            detail=word,
            wanted=wanted,
        )
        await self._registry.set_desired_level(node.device_id, None)

    async def _guarded(self, env: FlowEnv, node: SwarmNode, flow: Flow) -> None:
        """Run one flow; whatever it could not undo is recorded, and the device is released."""
        try:
            await flow.run()
        except (SwarmError, CellError, sqlite3.Error) as error:
            # The type only: a device error can carry text the device wrote.
            log.error(
                "swarm.rung_flow_failed", device_id=node.device_id, error=type(error).__name__
            )
            await self._settle(env, node, word_for(error))
        finally:
            self._flows.pop(node.device_id, None)
            self._arrivals.forget(node.device_id)
            self._wake()

    async def _settle(self, env: FlowEnv, node: SwarmNode, word: str) -> None:
        """Record a flow that failed outright and clear its request; never raises for the store."""
        try:
            await record_event(
                env.context.deps, "queen.decided", node.device_id, reason="rung_failed", detail=word
            )
            await self._registry.set_desired_level(node.device_id, None)
        except (SwarmError, sqlite3.Error) as error:
            log.error(
                "swarm.rung_settle_failed", device_id=node.device_id, error=type(error).__name__
            )


def _footprint(env: FlowEnv) -> RoleFootprint:
    """The footprint of one bee: what a Warden and a sub-bee are each counted as on a device."""
    return env.context.deps.footprints[WorkerRole.DRONE]
