"""Define the steps a colonization is built from: an order to the packet, a pause, a resume.

The flows move a device's Warden in a fixed order, and each step is small enough to be tested by
itself. `send_order` speaks `swarm.colonize` to the device's packet and returns what it answered,
turning every way an order can fail (the packet refused it, never answered it, or the link went)
into a `ColonizeStepError` with one stable word. `Move` is the state of one migration, owned by
the flow: the tasks it paused and the Handoff each one wrote, and how far it got, so an undo knows
what to undo. `pause_for_move` is Clustering's lever pulled for another cause (each bee of the
Warden checkpoints into a Handoff and stops, its task waits PAUSED) and notes, for each task, the
newest Handoff the Queen can hold for it, a Handoff only the Honey Store has included (`recall`),
and `resume_all` is the way back: every paused task is resumed through the dispatcher, which
carries the Handoff inline when
the Warden that takes over cannot read the Queen's memory store (a Warden on the device cannot),
and sends a Handoff that is tainted, or too large for one frame, as a fresh start with the reason
recorded. A task the dispatcher could not resume is returned to the caller, which takes it back so
that nothing stays PAUSED for good. `send_terms` is the node update on its own, for the callers
that must know it went, and `tell_terms`, which sends them only when the packet does not hold them
already (the terms renewal restarts a Warden from what the packet holds, so it may not go on if the
packet was never told), and `is_idle` is the test a withdrawal and the Queen's own
lowering of a rung both wait for: the device's Warden reports `WATCH` and holds no task.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Called by `.promote`, `.withdraw`, `.flow`, `.renew` and
    the Nuc flows. Calls into `hivemind.brood_chamber` (the task states), `hivemind.queen.attach`
    (detach, the Warden's state), `hivemind.queen.cluster` (the pause), `hivemind.queen.dispatcher`
    (resume), `hivemind.queen.swarm_gate` (the take-back and the recall of a Handoff), `hivemind.
    swarm` (the device link, its errors and the node update) and waggle only.

Key invariants:
    - Every order waits for the packet at most its own deadline plus a margin for the packet's
      FAILED reply to arrive; no step waits on a device indefinitely.
    - A failure of any order is one `ColonizeStepError`; the caller undoes what it did.
    - Pausing touches no grant and records no `queen.clustered`: nothing about a provider changed.
    - A Cell is held from the first pause until its tasks are resumed (or the flow ends), so
      nothing is placed, retried or resumed onto a Warden that is between machines.
    - A task that is resumed goes through the dispatcher's ordinary resume path, so its grant,
      its Honey pre-check and its Handoff are exactly a Clustering resume's.
    - The Handoff a paused task resumes from is the newest the Queen can hold, a Warden-side one
      recalled from Honey included, under the refusals the recall applies (Night Veil, tainted,
      written under a standing isolation, above the task's clearance).
    - Telling the device its new rung never raises for a link that is failing: the router sends
      the device its terms again whenever it connects. `send_terms` says whether they went.
    - Taking back the rest of a Cell's tasks leaves the ones the move paused and resumes alone.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the move: checkpoint, Handoff, resume.
    - hivemind.queen.swarm_gate.colonize.promote for the order the steps run in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hivemind.brood_chamber import TaskStatus
from hivemind.common.logging import get_logger
from hivemind.queen.attach import detach_warden, warden_state
from hivemind.queen.cluster import pause_on_warden
from hivemind.queen.dispatcher import resume_paused
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.recall import recall_handoff
from hivemind.queen.swarm_gate.takeback import take_back, tasks_on
from hivemind.queen.swarm_gate.terms import GateContext
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import (
    DeviceLink,
    DeviceRequestError,
    DeviceTimeoutError,
    GatewayHub,
    GatewayUnavailableError,
    SwarmNode,
)
from hivemind.swarm.enrolment import node_update_for
from waggle.ids import TaskId
from waggle.messages import HandoffRef
from waggle.messages.supervision import WardenState
from waggle.messages.swarm import Colonize, ColonizeAction, Colonized

# How long each order may take before the packet answers FAILED. PREPARE installs a runtime into a
# virtual environment, which may download and build; START only launches a process; WITHDRAW only
# stops one.
PREPARE_DEADLINE_S = 600.0
START_DEADLINE_S = 60.0
WITHDRAW_DEADLINE_S = 60.0
ATTACH_TIMEOUT_S = 90.0  # After START answered: the Warden it launched must still dial in.
REPLY_MARGIN_S = 15.0  # Past an order's own deadline: the packet answers FAILED at it, not before.
MOVING_REASON = "colonizing"  # Why the dispatcher leaves a Cell alone while its Warden moves.
# How long, for each task, a move waits for a bee that is inside a command to come back from it and
# write its Handoff. The Warden the move ends takes its bees with it, and a bee paused before it
# has written one resumes with nothing: a long command is the usual place to find a long task.
HANDOFF_PATIENCE_S = 30.0
# The words that mean the packet cannot be asked anything: sending it WITHDRAW would only wait out
# another deadline.
DEAD_LINK_WORDS = frozenset({"link_lost", "packet_silent"})

log = get_logger(__name__)

__all__ = [
    "ATTACH_TIMEOUT_S",
    "DEAD_LINK_WORDS",
    "HANDOFF_PATIENCE_S",
    "MOVING_REASON",
    "PREPARE_DEADLINE_S",
    "REPLY_MARGIN_S",
    "START_DEADLINE_S",
    "WITHDRAW_DEADLINE_S",
    "Move",
    "StepOrder",
    "dismiss_remote",
    "is_idle",
    "live_node",
    "pause_for_move",
    "resume_all",
    "send_order",
    "send_terms",
    "take_back_the_rest",
    "tell_device",
    "tell_terms",
]


@dataclass(frozen=True, slots=True)
class StepOrder:
    """One `swarm.colonize` order: which step, how long it may take, and what it needs.

    Attributes:
        action: PREPARE, START or WITHDRAW.
        deadline_s: Seconds the packet may take before it answers FAILED.
        reason: The Queen's decision in one short sentence.
        runtime_version: The runtime to install; required for PREPARE.
        warden_url: The URL the started Warden dials; required for START.
    """

    action: ColonizeAction
    deadline_s: float
    reason: str
    runtime_version: str | None = None
    warden_url: str | None = None


@dataclass(slots=True)
class Move:
    """One migration of a Warden onto a device, as the flow holds it between steps.

    Owns its own mutable state in place (codingrules 8.5): `paused`, `refs`, `moved` and
    `committed` advance as the flow does, and are what an undo reads to know how far it got.

    Attributes:
        node: The device as it was when the move began.
        link: The device's packet link, over which the orders go.
        paused: The tasks paused for the move, in the order they were paused.
        refs: Each paused task's newest Handoff the Queen can hold (one her own store has, or one
            recalled from the Honey Store), or None when she can hold none.
        moved: Whether the Level 0 Warden has been stopped, so the device has none on the Hive
            Stand until one is rebuilt.
        committed: Whether the registry already records the device on its new rung.
    """

    node: SwarmNode
    link: DeviceLink
    paused: tuple[TaskId, ...] = ()
    refs: dict[TaskId, HandoffRef | None] = field(default_factory=dict)
    moved: bool = False
    committed: bool = False


async def send_order(link: DeviceLink, node: SwarmNode, step: StepOrder) -> Colonized:
    """Send one `swarm.colonize` order over the packet's link and return its `swarm.colonized`.

    Args:
        link: The device's open packet link.
        node: The device being colonized.
        step: Which order to send.

    Returns:
        The packet's answer; its `outcome` says whether the step worked.

    Raises:
        ColonizeStepError: The packet refused the order (`packet_refused`), did not answer in time
            (`packet_silent`), the link was lost (`link_lost`) or the answer was not a
            `swarm.colonized` (`unexpected_reply`).
    """
    order = Colonize(
        cell_id=node.cell_id,
        device_id=node.device_id,
        action=step.action,
        warden_id=node.warden_id,
        runtime_version=step.runtime_version,
        warden_url=step.warden_url,
        deadline_s=step.deadline_s,
        reason=step.reason,
    )
    try:
        reply = await link.request(
            order, sender=link.hive_id, timeout_s=step.deadline_s + REPLY_MARGIN_S
        )
    except DeviceRequestError as error:
        raise ColonizeStepError("packet_refused") from error
    except DeviceTimeoutError as error:
        raise ColonizeStepError("packet_silent") from error
    except GatewayUnavailableError as error:
        raise ColonizeStepError("link_lost") from error
    if not isinstance(reply.payload, Colonized):
        raise ColonizeStepError("unexpected_reply")
    return reply.payload


async def send_terms(link: DeviceLink, node: SwarmNode, reason: str) -> bool:
    """Send the packet `swarm.node_update`: its terms as the registry now holds them.

    Args:
        link: The device's packet link.
        node: The device as the registry now holds it.
        reason: Why its terms changed, in one short sentence.

    Returns:
        True when the link took the frame; False when it could not (the link is failing or the
        device stopped reading), in which case the packet has not been told anything.
    """
    try:
        await link.send(node_update_for(node, reason), sender=link.hive_id)
    except (GatewayUnavailableError, DeviceTimeoutError):
        return False
    return True


async def tell_terms(hub: GatewayHub, link: DeviceLink, node: SwarmNode, reason: str) -> bool:
    """Make sure the packet holds the terms `node` carries, sending them unless it already does.

    The hub's record of the device is the terms it was last told: it is replaced only after a send
    went, so a record that already carries these terms means the packet has them, and a pass that
    finds the registry's different sends them and records that it did.

    Args:
        hub: Where the connected devices' latest records are kept.
        link: The device's packet link.
        node: The device as the registry now holds it.
        reason: Why its terms changed, in one short sentence.

    Returns:
        True when the packet holds the terms (it did, or was just sent them); False when the link
        could not take them, in which case nothing is assumed and the next pass tries again.
    """
    known = hub.node(node.device_id)
    if (
        known is not None
        and known.access_level is node.access_level
        and known.comb_shield is node.comb_shield
    ):
        return True
    if not await send_terms(link, node, reason):
        return False
    hub.update_node(live_node(hub, node))
    return True


async def tell_device(link: DeviceLink, node: SwarmNode, reason: str) -> None:
    """Send the device its new rung over the packet's link; a failing link is left to the router.

    Args:
        link: The device's packet link.
        node: The device as the registry now holds it.
        reason: Why its terms changed, in one short sentence.
    """
    if not await send_terms(link, node, reason):
        # The router sends a device its current terms whenever it connects, so a packet that
        # missed this one learns its rung then.
        log.warning("swarm.node_update_not_sent", device_id=node.device_id)


def live_node(hub: GatewayHub, node: SwarmNode) -> SwarmNode:
    """Return `node` with the capacity the hub last had from the device's packet.

    The registry's row says what was asked and decided; the hub holds the capacity last reported,
    and a decision about what a device can carry is made on that.

    Args:
        hub: Where the connected devices' latest records are kept.
        node: The device as the registry holds it.

    Returns:
        `node`, with the hub's capacity when the hub holds the device.
    """
    latest = hub.node(node.device_id)
    return node if latest is None else node.model_copy(update={"capacity": latest.capacity})


async def is_idle(context: GateContext, node: SwarmNode) -> bool:
    """Whether the device's own Warden reports `WATCH` and holds no task: nothing to lose by a move.

    Args:
        context: The Queen and her collaborators.
        node: A device whose Warden runs on it.

    Returns:
        True when the Warden's newest Heartbeat says it observes and no unfinished task is placed
        on its Cell. A Warden that has not reported yet is not idle.
    """
    state = warden_state(context.queen, node.warden_id)
    return state is WardenState.WATCH and not await tasks_on(context.deps, node)


async def pause_for_move(context: GateContext, move: Move, reason: str) -> None:
    """Hold the Cell, then have every running bee of the device's Warden write a Handoff and stop.

    The Cell is held first (`DispatchBook.held`) so that, while its Warden is between machines,
    the dispatcher places, retries and resumes nothing onto it; `resume_all` opens it again. A bee
    that is inside a command is waited for, up to `HANDOFF_PATIENCE_S` for each task, because it
    can write its Handoff only when the command returns and the Warden's end takes it with it.

    Args:
        context: The Queen and her collaborators.
        move: The migration; its `paused` and `refs` are filled.
        reason: Why, folded into every order and the chamber's own event.
    """
    deps, node = context.deps, move.node
    deps.dispatch.held.hold(node.cell_id, MOVING_REASON)
    move.paused = await pause_on_warden(
        deps, context.queen.wardens, node.warden_id, reason, patience_s=HANDOFF_PATIENCE_S
    )
    for task_id in move.paused:
        # Not the Handoff in her own Bee Bread alone: a colonized Warden's bees wrote their newest
        # in the device's store, and what reaches her is the Nectar each deposited (ADR-0053), so
        # a renewal resumes from that, under the refusals every recall applies, and not from the
        # older Handoff the colonization carried. Nothing is lost by a refusal: the task resumes
        # from the one she holds, or starts fresh.
        move.refs[task_id] = await recall_handoff(
            deps, node.cell_id, await deps.chamber.get(task_id)
        )


async def resume_all(context: GateContext, move: Move, reason: str) -> tuple[TaskId, ...]:
    """Open the Cell again and resume every task the move paused, from its Handoff.

    The tasks go to whichever Warden now holds the Cell: the device's own after a promotion, the
    Level 0 Warden after an undo.

    Args:
        context: The Queen and her collaborators.
        move: The migration whose paused tasks are resumed.
        reason: Why they resume now, for the chamber's own event.

    Returns:
        The tasks still PAUSED afterwards (the Warden was not there to resume them through), for
        the caller to take back; empty when every one was resumed or has ended.
    """
    context.deps.dispatch.held.release(move.node.cell_id)  # Open again: a resume may reach it.
    for task_id in move.paused:
        ref = move.refs.get(task_id)
        await resume_paused(context.deps, context.queen.wardens, task_id, ref, reason)
    stranded = [
        task_id
        for task_id in move.paused
        if (await context.deps.chamber.get(task_id)).status is TaskStatus.PAUSED
    ]
    return tuple(stranded)


async def take_back_the_rest(context: GateContext, move: Move, cause: str) -> None:
    """Take back what the pause left on the Cell: assigned or blocked work has no bee to resume.

    Args:
        context: The Queen and her collaborators.
        move: The migration; the tasks it paused are left alone, since they resume from Handoffs.
        cause: A short stable word for why they are taken back, recorded.
    """
    deps = context.deps
    tasks = await tasks_on(deps, move.node)
    rest = {t.id for t in tasks if t.id not in move.paused and t.status is not TaskStatus.PAUSED}
    if rest:
        await take_back(deps, move.node, cause, only=rest)


async def dismiss_remote(context: GateContext, wardens: DeviceWardens, node: SwarmNode) -> None:
    """Forget the Warden that dialled in from the device, and close its connection.

    Args:
        context: The Queen the Warden is attached to.
        wardens: Where the Queen's link to the device's Warden is found.
        node: The device whose own Warden is let go.
    """
    link = wardens.link_of(node.warden_id)
    if link is None:
        return  # It never attached, or is already gone.
    await detach_warden(context.queen, node.warden_id)
    await link.transport.close()
