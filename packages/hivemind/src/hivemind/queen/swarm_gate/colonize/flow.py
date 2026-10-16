"""Define Colonizer: carry out the rung changes of a device, one flow at a time.

Four things start a flow on a device, and the gate's reconcile pass asks the `Colonizer` for each.
The operator's `desired_level` (`request`): the Colonizer decides (`.request.decide_request`, pure),
asks the Guard whether the Queen may change this device's rung (`.guard.authorise_rung`, for a rung
up and a rung down alike), and starts the flow, `Promotion` or `Withdrawal` for the first rung and
`NucPromotion` or `NucDemotion` for the second (`.nuc`). The Queen's own decisions (`launch`), which
`hivemind.queen.swarm_gate.rungs` makes and which are the same flows behind the same Guard check, so
a move she decides and a move the operator asked for are one code path. A colonized device whose
Warden runs under terms the operator has changed (`renew`): the Warden is restarted under the new
ones (`.renew`). And a Nuc whose Warden connected afresh and has not shown its model server running
(`restore`): the server is started again, or the rung lowered (`.nuc.restore`). Each runs as a task
of the gate's group: a colonization can spend minutes installing a runtime on the device, and the
gate's loop must go on serving every other device while it does. While a flow runs it owns the
device: the reconcile pass leaves the node alone (`busy`), and the gate's door admits the Warden the
flow started (`expects`), reports its arrival (`arrived`), hands over what the Warden says (`heard`:
its declared model servers, the terms it was started under, and the answers to the orders a Nuc flow
sent it) and reports its loss (`lost`).

A request is answered once. If it is refused (the device is too small, its Warden is busy, the
Guard said no, no model server fits) the Queen records one `queen.decided` row with the reason's
word and clears the request, so a refusal costs one row and not one per pass. If a flow fails, the
flow itself undoes the move and clears the request; if something outside the flow's own handling
goes wrong, this module records it and clears the request too. A move the Queen decided herself
has no request to clear, and a refusal of hers is returned as a word for her to remember.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Built by `hivemind.queen.swarm_gate.gate.SwarmGate`,
    bound to the Queen when the gate starts, called by `hivemind.queen.swarm_gate.reconcile`,
    `hivemind.queen.swarm_gate.rungs` and by the gate's door. Calls into `hivemind.queen.attach`
    (the Warden's reported state), `.nuc`, `.renew`, `hivemind.queen.swarm_gate` (its own modules),
    `hivemind.queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - At most one flow runs per device, and a device with a flow running is skipped by the
      reconcile pass.
    - Nothing is sent to a device, paused or stopped before the Guard has said yes, for a rung
      change; a terms renewal and a restoration change no rung and ask the Guard nothing.
    - A withdrawal is ordered only of a Warden that reports `WATCH` and has no task on its Cell.
    - Every flow ends by waking the gate's loop, so the device's next state is looked at at once.
    - `stop` cancels every flow still running and waits for it; each cancelled promotion takes
      the tasks it paused back to PENDING.

See Also:
    - hivemind.queen.swarm_gate.colonize.request for the pure decision.
    - hivemind.queen.swarm_gate.colonize.promote and .withdraw for the flows.
    - hivemind.queen.swarm_gate.rungs for the Queen's own decisions that `launch` carries out.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from functools import partial
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.forage import RoleFootprint
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize.arrivals import Arrivals
from hivemind.queen.swarm_gate.colonize.env import FlowEnv
from hivemind.queen.swarm_gate.colonize.errors import word_for
from hivemind.queen.swarm_gate.colonize.guard import authorise_rung
from hivemind.queen.swarm_gate.colonize.nuc.demote import NucDemotion
from hivemind.queen.swarm_gate.colonize.nuc.desk import NucDesk
from hivemind.queen.swarm_gate.colonize.nuc.facts import nuc_decision
from hivemind.queen.swarm_gate.colonize.nuc.promote import NucPromotion
from hivemind.queen.swarm_gate.colonize.nuc.restore import NucRestoration
from hivemind.queen.swarm_gate.colonize.promote import Promotion
from hivemind.queen.swarm_gate.colonize.renew.drift import terms_drifted
from hivemind.queen.swarm_gate.colonize.renew.flow import Renewal
from hivemind.queen.swarm_gate.colonize.request import Ask, Step, decide_request
from hivemind.queen.swarm_gate.colonize.steps import is_idle, live_node
from hivemind.queen.swarm_gate.colonize.withdraw import Withdrawal
from hivemind.queen.swarm_gate.errors import RECOVERABLE_ERRORS
from hivemind.queen.swarm_gate.terms import GateContext, GateTerms
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.queen.trail import record_event
from hivemind.swarm import (
    DeviceLink,
    RuntimeLevel,
    SwarmError,
    SwarmListener,
    SwarmNode,
    SwarmRegistry,
)
from waggle.envelope import Envelope
from waggle.ids import DeviceId
from waggle.messages.task import WorkerRole

log = get_logger(__name__)

GUARD_DENIED_WORD = "guard_denied"  # The Guard did not let the Queen change the device's rung.
NOT_READY_WORD = "not_ready"  # The device or its Warden is not reachable yet: nothing was started.
OWNED_WORD = "owned"  # A flow already owns the device: nothing was started.

__all__ = ["GUARD_DENIED_WORD", "NOT_READY_WORD", "OWNED_WORD", "Colonizer", "Flow"]


class Flow(Protocol):
    """One rung change in progress: it runs to its end and raises only what it could not undo."""

    async def run(self) -> None:
        """Carry out the flow; see `Promotion.run` and `Withdrawal.run`."""
        ...


class Colonizer:
    """Decide, authorise and run the rung changes of devices, one flow per device."""

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
            wake: Wakes the gate's loop whenever a flow ends.
        """
        self._wardens = wardens
        self._registry = registry
        self._listener = listener
        self._terms = terms
        self._wake = wake
        self._arrivals = Arrivals()
        self._desk = NucDesk()
        self._env: FlowEnv | None = None
        self._group: asyncio.TaskGroup | None = None
        self._flows: dict[DeviceId, asyncio.Task[None]] = {}

    def bind(self, context: GateContext) -> None:
        """Work against `context`'s Queen from now on."""
        self._env = FlowEnv(
            context,
            self._wardens,
            self._registry,
            self._listener,
            self._terms,
            self._arrivals,
            self._desk,
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

    def declared(self, device_id: DeviceId) -> tuple[str, ...]:
        """Return the model server ids `device_id`'s Warden last declared, if any."""
        return self._desk.declared(device_id)

    def heard(self, node: SwarmNode, envelope: Envelope) -> bool:
        """Take in one frame of `node`'s Warden; True when the gate consumed it.

        A `cell.ready` (the terms) and a capacity report (the servers) are noted and go on to the
        Queen; a `swarm.nuc_promoted` answers a Nuc flow's order and ends here.
        """
        return self._desk.heard(node, envelope)

    def lost(self, device_id: DeviceId) -> None:
        """Report that `device_id`'s Warden's connection ended; whatever waits on it stops."""
        self._desk.lost(device_id)

    async def request(self, node: SwarmNode) -> bool:
        """Carry out or refuse the rung `node` asks for; True when a flow now owns the device.

        False when nothing was asked, the device has no live packet link yet (the request waits),
        the gate is not running, or the request was refused (recorded once, and cleared).
        """
        env = self._env
        if env is None or self._listener.hub.link(node.device_id) is None:
            return False
        if node.desired_level is None:
            return False
        if self.busy(node.device_id):
            return True
        ask = await ask_for(env, node)
        if ask.step is None:
            await refuse(env, node, ask.word)
            return False
        word = await self.launch(node, ask)
        if word == GUARD_DENIED_WORD:
            await refuse(env, node, word)
        return word is None

    async def launch(self, node: SwarmNode, ask: Ask) -> str | None:
        """Start the flow `ask` calls for, behind the Guard; the Queen's own moves enter here.

        The operator's request and the Queen's own decision (`hivemind.queen.swarm_gate.rungs`)
        are one path from here on. Nothing is answered or cleared: a caller with a request to
        clear does it, and one without remembers the refusal.

        Returns:
            None when a flow now owns the device; otherwise why nothing started: `guard_denied`,
            `not_ready` (no packet link, no Warden to speak to, or the gate is not running) or
            `owned` (a flow already has the device).
        """
        env, hub = self._env, self._listener.hub
        link = hub.link(node.device_id)
        if env is None or ask.step is None or link is None or self._group is None:
            return NOT_READY_WORD
        if self.busy(node.device_id):
            return OWNED_WORD
        if not await authorise_rung(env.context.deps, node):
            return GUARD_DENIED_WORD
        flow = flow_for(env, ask, (live_node(hub, node), link))
        if flow is None or not self._begin(env, flow, node):
            return NOT_READY_WORD  # A Nuc rung with no Warden attached right now: it waits.
        return None

    async def renew(self, node: SwarmNode) -> bool:
        """Restart a colonized device's Warden if it runs under terms the operator changed.

        True when a renewal now owns the device (or already did); False when the Warden runs
        under the registry's terms, has reported none, or cannot be reached yet.
        """
        env = self._env
        if env is None or node.level is RuntimeLevel.GATEWAY:
            return False
        if self.busy(node.device_id):
            return True
        if not terms_drifted(self._desk.reported(node.device_id), node):
            return False
        link, warden = self._listener.hub.link(node.device_id), self._connected_warden(env, node)
        if link is None or warden is None:
            return False  # Told when it can be: the next pass finds the difference again.
        return self._begin(env, Renewal(env, live_node(self._listener.hub, node), link), node)

    async def restore(self, node: SwarmNode) -> bool:
        """Start a Nuc's model server again if its Warden connected without showing it running.

        True when a restoration now owns the device (or already did); False when the device is
        not a Nuc, its server is proven, or its Warden cannot be reached yet.
        """
        env = self._env
        if env is None or node.level is not RuntimeLevel.NUC:
            return False
        if self.busy(node.device_id):
            return True
        warden = self._connected_warden(env, node)
        if warden is None or self._desk.is_proven(node.device_id):
            return False
        return self._begin(env, NucRestoration(env, node, warden), node)

    async def stop(self) -> None:
        """Cancel every flow still running and wait for each to undo what it must."""
        flows = tuple(self._flows.values())
        for task in flows:
            task.cancel()
        await asyncio.gather(*flows, return_exceptions=True)

    def _begin(self, env: FlowEnv, flow: Flow, node: SwarmNode) -> bool:
        """Run `flow` as a task of the gate's group, owning the device; False with no group."""
        group = self._group
        if group is None:
            return False
        ended = partial(self._ended, node.device_id)
        self._flows[node.device_id] = group.create_task(run_flow(env, node, flow, ended))
        return True

    def _ended(self, device_id: DeviceId) -> None:
        """Release a device whose flow ended, and wake the gate's loop to look at it."""
        self._flows.pop(device_id, None)
        self._arrivals.forget(device_id)
        self._wake()

    def _connected_warden(self, env: FlowEnv, node: SwarmNode) -> WardenLink | None:
        """Return the link of the device's own Warden if it is connected to the Queen right now."""
        warden = env.wardens.link_of(node.warden_id)
        return warden if warden is not None and warden.transport.is_connected else None


async def ask_for(env: FlowEnv, node: SwarmNode) -> Ask:
    """What the operator's pending request asks of the Queen right now, from what the gate knows."""
    idle = node.level is RuntimeLevel.COLONIZED and await is_idle(env.context, node)
    # The registry's node says what is asked; the hub holds the capacity last reported.
    basis = live_node(env.listener.hub, node)
    nuc = nuc_decision(basis, env.desk.declared(node.device_id), env.terms)
    return decide_request(node, env.terms, _footprint(env), is_idle=idle, nuc=nuc)


def flow_for(env: FlowEnv, ask: Ask, device: tuple[SwarmNode, DeviceLink]) -> Flow | None:
    """Build the flow `ask` calls for on `device`, or None when its Warden is not attached."""
    node, link = device
    if ask.step is Step.PROMOTE:
        return Promotion(env, node, link, ask.word)
    if ask.step is Step.WITHDRAW:
        return Withdrawal(env, node, link, ask.word)
    # The Nuc rungs speak to the device's own Warden, which is not the packet.
    warden = env.wardens.link_of(node.warden_id)
    if warden is None or not warden.transport.is_connected:
        return None
    if ask.step is Step.NUC_PROMOTE and ask.decision is not None:
        return NucPromotion(env, node, warden, ask.decision)
    return NucDemotion(env, node, warden, ask.word)


async def run_flow(env: FlowEnv, node: SwarmNode, flow: Flow, ended: Callable[[], None]) -> None:
    """Run one flow; whatever it could not undo is recorded, and the device is released."""
    try:
        await flow.run()
    except RECOVERABLE_ERRORS as error:
        # The type only: a device error can carry text the device wrote.
        log.error("swarm.rung_flow_failed", device_id=node.device_id, error=type(error).__name__)
        await settle(env, node, word_for(error))
    finally:
        ended()


async def refuse(env: FlowEnv, node: SwarmNode, word: str) -> None:
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
    await env.registry.set_desired_level(node.device_id, None)


async def settle(env: FlowEnv, node: SwarmNode, word: str) -> None:
    """Record a flow that failed outright and clear its request; never raises for the store."""
    try:
        await record_event(
            env.context.deps, "queen.decided", node.device_id, reason="rung_failed", detail=word
        )
        await env.registry.set_desired_level(node.device_id, None)
    except (SwarmError, sqlite3.Error) as error:
        log.error("swarm.rung_settle_failed", device_id=node.device_id, error=type(error).__name__)


def _footprint(env: FlowEnv) -> RoleFootprint:
    """The footprint of one bee: what a Warden and a sub-bee are each counted as on a device."""
    return env.context.deps.footprints[WorkerRole.DRONE]
