"""Define UnreachableDevices: hold a lost device's tasks, wait out the grace, then decide.

A device is somebody's machine and usually comes back, so losing it is not failing it. When a
device is no longer reachable (its packet's link ended or fell silent, or the Warden on it lost its
link to the Queen) this class starts a hold on it: the device's Cell is marked held for the
dispatcher (`DispatchBook.held`: no fresh placement, no retry, no resume goes to it), a Warden
that runs here is told its Cell was lost (it raises one `CELL_UNREACHABLE` Alarm and treats its
lease as lost until the device returns), a device whose Warden is on it and whose packet is still
heard is marked UNREACHABLE (`swarm.unreachable`; its heartbeats would otherwise keep calling it
online), the hold is on the trail (`swarm.node_held`), and, when the hold holds tasks and
somebody saw the device go, the Hive Stand-side diagnosis is asked for (`Diagnosis`; the Hive
installs `.hive_stand`): with nothing held nobody waits on its answer, and a device nobody saw go,
one just enrolled whose packet has not dialled yet or one the Hive restarted without, is not lost,
only not here yet, so a Hive that has just started sends the Hive Stand no Drone for it. The hold
then waits `[swarm] unreachable_grace_s`. A device that returns inside it cancels the hold and
nothing was lost. When the grace ends the Queen decides by the device's rung (`decide.verdict_for`):
a Level 0 or Level 1 device's tasks are taken back and placed afresh from their last Handoff, its
Warden let go; a Nuc's tasks keep waiting, up to `[supervision] max_offline_s`, because it is still
working. Every decision is a `queen.decided` event. A device that comes back after its tasks were
taken has the Warden it still had told to cancel them, so nothing runs twice; its Cell stays held
from the loss until those cancels went, so the Queen's tick cannot place one of those very tasks
on the returning Warden a moment before the cancel for it arrives.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.unreachable`. Called by the gate's reconcile pass (`lost`, `found`
    for a node it finds unreachable or back), by the device Warden door (`warden_lost`, `found`)
    and by the gate itself, which notes why a device went. Calls into `hivemind.queen.attach`,
    `.swarm_gate.takeback`, `.swarm_gate.wardens`, `.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - `lost` and `found` are idempotent, so the level-triggered reconcile pass can call them on
      every pass: a hold begins once, is decided once, and ends once.
    - Nothing is failed or cancelled during the hold, and the dispatcher acts on a held Cell only
      after it is released: when the device is found (after the cancels for its abandoned tasks
      went) or forgotten, never at the decision and never at the Warden's attach.
    - A decision is made at most once per hold, from the device's rung when the grace ended.
    - A device with no hold, and no Warden here, is untouched by `found`.
    - Why a device went is a short stable word the hub gave; never an address, a key or a path.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the hold and the decision.
    - hivemind.queen.swarm_gate.takeback for how the tasks are taken back.
    - hivemind.queen.swarm_gate.unreachable.decide for the verdicts and the two limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from hivemind.queen.swarm_gate.takeback import take_back, tasks_on
from hivemind.queen.swarm_gate.terms import GateContext, GateTerms
from hivemind.queen.swarm_gate.unreachable.decide import (
    Verdict,
    grace_over,
    offline_over,
    verdict_for,
)
from hivemind.queen.swarm_gate.unreachable.diagnosis import Diagnosis, NoDiagnosis
from hivemind.queen.swarm_gate.unreachable.effects import (
    cancel_abandoned,
    event_ids,
    let_go,
    mark_unreachable,
)
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.queen.trail import record_event
from hivemind.swarm import (
    RuntimeLevel,
    SwarmNode,
    SwarmRecorder,
    SwarmRegistry,
)
from waggle.ids import DeviceId, TaskId

# What an Alarm and the trail say when the hub gave no reason, e.g. a device the reconcile pass
# finds with no live link after a notice was missed, or one the Hive restarted without.
NO_LINK_REASON = "no_live_link"
WARDEN_LINK_REASON = "warden_link_closed"  # A device's Warden lost its own link to the Queen.

__all__ = [
    "NO_LINK_REASON",
    "WARDEN_LINK_REASON",
    "HoldState",
    "UnreachableDevices",
]


class HoldState(Enum):
    """Where one device's hold is: inside the grace, waiting on a Nuc, or decided and done."""

    HELD = "held"  # Inside the grace: nothing is decided yet.
    WAITING = "waiting"  # A Nuc past its grace: kept waiting up to the offline limit.
    REPLACED = "replaced"  # Its tasks were taken back and its Warden let go.


@dataclass(slots=True)
class _Hold:
    """One lost device's hold: when it began, why, where it is, and what it left behind.

    Owns its own mutable state in place (codingrules 8.5): the state advances as the grace ends
    and the Queen decides; `abandoned` is filled by the decision to take the tasks back.
    """

    since: datetime
    reason: str
    state: HoldState = HoldState.HELD
    abandoned: tuple[TaskId, ...] = ()
    is_diagnosing: bool = False


class UnreachableDevices:
    """Hold a lost device's tasks, and decide what to do with them when the grace ends."""

    def __init__(
        self,
        wardens: DeviceWardens,
        registry: SwarmRegistry,
        recorder: SwarmRecorder,
        terms: GateTerms,
        diagnosis: Diagnosis | None = None,
    ) -> None:
        """Build the handler over the table of device Wardens and the registry.

        Args:
            wardens: Where a device's Warden is found, and stopped.
            registry: Where a device's status is moved to UNREACHABLE when its Warden is lost.
            recorder: The Swarm's writer of `swarm.*` events.
            terms: The grace, the offline limit and the other numbers.
            diagnosis: The Hive Stand-side diagnosis asked for during a hold; none by default.
        """
        self._wardens = wardens
        self._registry = registry
        self._recorder = recorder
        self._terms = terms
        self._diagnosis: Diagnosis = diagnosis if diagnosis is not None else NoDiagnosis()
        self._context: GateContext | None = None
        self._holds: dict[DeviceId, _Hold] = {}
        self._reasons: dict[DeviceId, str] = {}

    def bind(self, context: GateContext) -> None:
        """Hold devices' tasks against `context`'s Queen from now on."""
        self._context = context

    def use_diagnosis(self, diagnosis: Diagnosis) -> None:
        """Ask `diagnosis` for a reason during every hold that begins from now on."""
        self._diagnosis = diagnosis

    def note(self, device_id: DeviceId, reason: str) -> None:
        """Remember why a device just went away, for the Alarm and the hold that follow.

        Args:
            device_id: The device.
            reason: The hub's short reason for the loss.
        """
        self._reasons[device_id] = reason

    def hold_of(self, device_id: DeviceId) -> HoldState | None:
        """Return where the device's hold is, or None when it has none."""
        hold = self._holds.get(device_id)
        return hold.state if hold is not None else None

    async def lost(self, node: SwarmNode) -> None:
        """Hold the lost device's tasks, and decide once the grace has ended.

        Called on every reconcile pass that finds the device unreachable; the hold begins on the
        first and the rest only look at the clock.

        Args:
            node: The device that is not reachable.
        """
        served = self._wardens.get(node.device_id)
        if served is not None:
            await served.warden.cell_lost(self._reasons.get(node.device_id, NO_LINK_REASON))
        context = self._context
        if context is None:
            return  # Not bound to a Queen yet: the Warden was told, and nothing more can be done.
        hold = self._holds.get(node.device_id) or await self._begin(context, node)
        held_s = (context.deps.clock.now() - hold.since).total_seconds()
        if hold.state is HoldState.HELD and grace_over(held_s, self._terms.unreachable_grace_s):
            await self._decide(context, node, hold)
        elif hold.state is HoldState.WAITING and offline_over(held_s, self._terms.max_offline_s):
            await self._replace(context, node, hold, "max_offline")

    async def warden_lost(self, node: SwarmNode, reason: str = WARDEN_LINK_REASON) -> None:
        """Hold the tasks of a device whose own Warden just lost its link to the Queen.

        Args:
            node: The device whose Warden dialled in and has now gone.
            reason: Why, as a short stable word.
        """
        self.note(node.device_id, reason)
        await self.lost(node)

    async def found(self, node: SwarmNode) -> None:
        """End the device's hold: it is reachable, or its Warden is back.

        Args:
            node: The device that is reachable again.
        """
        self._reasons.pop(node.device_id, None)
        served = self._wardens.get(node.device_id)
        if served is not None:
            served.warden.cell_found()
        hold, context = self._holds.pop(node.device_id, None), self._context
        if hold is None or context is None:
            return
        await self._conclude(node, hold)
        # The cancels first, the Cell open second: the returning Warden must hear that the Queen
        # placed its abandoned tasks elsewhere before her tick may place anything on it, or the
        # cancel would reach the fresh bee of a task the tick had just put back on that Warden.
        await cancel_abandoned(context, self._wardens, node, hold.abandoned)
        context.deps.dispatch.held.release(node.cell_id)
        detail = "after_replacement" if hold.state is HoldState.REPLACED else "reconnected"
        await record_event(
            context.deps, "queen.decided", node.device_id, reason="hold_released", detail=detail
        )

    async def forget(self, node: SwarmNode) -> None:
        """End a revoked device's hold without a decision: there is nothing left to wait for.

        Args:
            node: The device the operator revoked.
        """
        self._reasons.pop(node.device_id, None)
        hold, context = self._holds.pop(node.device_id, None), self._context
        if context is not None:
            context.deps.dispatch.held.release(node.cell_id)
        if hold is not None:
            await self._conclude(node, hold)

    async def _begin(self, context: GateContext, node: SwarmNode) -> _Hold:
        """Start a hold: hold the Cell, mark the node, say so on the trail, ask for a diagnosis."""
        reason = self._reasons.get(node.device_id, NO_LINK_REASON)
        held = len(await tasks_on(context.deps, node))
        # A diagnosis only where someone waits on its answer (a hold that holds tasks) and
        # something went to look into: a device nobody saw go (just enrolled, whose packet has not
        # dialled yet, or one the Hive restarted without) is not lost, only not here yet, and a
        # Drone for it would be a model run for a race of a few seconds. Its Cell is still held
        # and the grace still decides, as for any device that is not reachable.
        asked = held > 0 and reason != NO_LINK_REASON
        hold = _Hold(since=context.deps.clock.now(), reason=reason, is_diagnosing=asked)
        self._holds[node.device_id] = hold
        context.deps.dispatch.held.hold(node.cell_id, reason)
        if node.level is not RuntimeLevel.GATEWAY:
            await mark_unreachable(context, self._registry, self._recorder, node, reason)
        payload = event_ids(node, reason) | {
            "level": node.level.value,
            "grace_s": self._terms.unreachable_grace_s,
            "tasks": held,
        }
        await self._recorder.record(
            self._recorder.swarm_event("swarm.node_held", node.device_id, payload)
        )
        if hold.is_diagnosing:
            await self._diagnosis.begin(node, reason)
        return hold

    async def _decide(self, context: GateContext, node: SwarmNode, hold: _Hold) -> None:
        """The grace is over: decide by the device's rung, and record the decision."""
        verdict = verdict_for(node.level)
        await record_event(
            context.deps,
            "queen.decided",
            node.device_id,
            reason="unreachable_decision",
            detail=verdict.value,
            level=node.level.value,
        )
        if verdict is Verdict.REPLACE:
            await self._replace(context, node, hold, "grace_over")
        else:
            hold.state = HoldState.WAITING

    async def _replace(
        self, context: GateContext, node: SwarmNode, hold: _Hold, cause: str
    ) -> None:
        """Take the device's tasks back and let its Warden go; the Cell stays held until found."""
        hold.abandoned = await take_back(context.deps, node, cause)
        await let_go(context, self._wardens, node)
        # Not released here: no Warden is attached for the Cell, so nothing could be placed on it
        # anyway, and `found` opens it once the returning Warden has been told what to cancel.
        hold.state = HoldState.REPLACED
        await self._conclude(node, hold)

    async def _conclude(self, node: SwarmNode, hold: _Hold) -> None:
        """End the diagnosis of this hold, once, whichever way the hold ended."""
        if hold.is_diagnosing:
            hold.is_diagnosing = False
            await self._diagnosis.end(node)
