"""Define the steps a colonization is built from: an order to the packet, a pause, a resume.

The flows move a device's Warden in a fixed order, and each step is small enough to be tested by
itself. `send_order` speaks `swarm.colonize` to the device's packet and returns what it answered,
turning every way an order can fail (the packet refused it, never answered it, or the link went)
into a `ColonizeStepError` with one stable word. `Move` is the state of one migration, owned by
the flow: the tasks it paused and the Handoff each one wrote, and how far it got, so an undo knows
what to undo. `pause_for_move` is Clustering's lever pulled for another cause (each bee of the
Warden checkpoints into a Handoff and stops, its task waits PAUSED), and `resume_all` is the way
back: every paused task is resumed through the dispatcher, which carries the Handoff inline when
the Warden that takes over cannot read the Queen's memory store (a Warden on the device cannot),
and sends a Handoff that is tainted, or too large for one frame, as a fresh start with the reason
recorded. A task the dispatcher could not resume is returned to the caller, which takes it back so
that nothing stays PAUSED for good.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Called by `.promote` and `.withdraw`. Calls into
    `hivemind.brood_chamber` (the task states), `hivemind.queen.attach` (detach),
    `hivemind.queen.cluster` (the pause and the newest Handoff), `hivemind.queen.dispatcher`
    (resume), `hivemind.swarm` (the device link, its errors and the node update) and waggle only.

Key invariants:
    - Every order waits for the packet at most its own deadline plus a margin for the packet's
      FAILED reply to arrive; no step waits on a device indefinitely.
    - A failure of any order is one `ColonizeStepError`; the caller undoes what it did.
    - Pausing touches no grant and records no `queen.clustered`: nothing about a provider changed.
    - A Cell is held from the first pause until its tasks are resumed (or the flow ends), so
      nothing is placed, retried or resumed onto a Warden that is between machines.
    - A task that is resumed goes through the dispatcher's ordinary resume path, so its grant,
      its Honey pre-check and its Handoff are exactly a Clustering resume's.
    - Telling the device its new rung never raises for a link that is failing: the router sends
      the device its terms again whenever it connects.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the move: checkpoint, Handoff, resume.
    - hivemind.queen.swarm_gate.colonize.promote for the order the steps run in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hivemind.brood_chamber import TaskStatus
from hivemind.common.logging import get_logger
from hivemind.queen.attach import detach_warden
from hivemind.queen.cluster import latest_handoff, pause_on_warden
from hivemind.queen.dispatcher import resume_paused
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.terms import GateContext
from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import (
    DeviceLink,
    DeviceRequestError,
    DeviceTimeoutError,
    GatewayUnavailableError,
    SwarmNode,
)
from hivemind.swarm.enrolment import node_update_for
from waggle.ids import TaskId
from waggle.messages import HandoffRef
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

log = get_logger(__name__)

__all__ = [
    "ATTACH_TIMEOUT_S",
    "MOVING_REASON",
    "PREPARE_DEADLINE_S",
    "REPLY_MARGIN_S",
    "START_DEADLINE_S",
    "WITHDRAW_DEADLINE_S",
    "Move",
    "StepOrder",
    "dismiss_remote",
    "pause_for_move",
    "resume_all",
    "send_order",
    "tell_device",
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
        refs: Each paused task's newest Handoff, or None when its bee wrote none.
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


async def tell_device(link: DeviceLink, node: SwarmNode, reason: str) -> None:
    """Send the device its new rung over the packet's link; a failing link is left to the router.

    Args:
        link: The device's packet link.
        node: The device as the registry now holds it.
        reason: Why its terms changed, in one short sentence.
    """
    try:
        await link.send(node_update_for(node, reason), sender=link.hive_id)
    except (GatewayUnavailableError, DeviceTimeoutError):
        # The router sends a device its current terms whenever it connects, so a packet that
        # missed this one learns its rung then.
        log.warning("swarm.node_update_not_sent", device_id=node.device_id)


async def pause_for_move(context: GateContext, move: Move, reason: str) -> None:
    """Hold the Cell, then have every running bee of the device's Warden write a Handoff and stop.

    The Cell is held first (`DispatchBook.held`) so that, while its Warden is between machines,
    the dispatcher places, retries and resumes nothing onto it; `resume_all` opens it again.

    Args:
        context: The Queen and her collaborators.
        move: The migration; its `paused` and `refs` are filled.
        reason: Why, folded into every order and the chamber's own event.
    """
    deps, node = context.deps, move.node
    deps.dispatch.held.hold(node.cell_id, MOVING_REASON)
    move.paused = await pause_on_warden(deps, context.queen.wardens, node.warden_id, reason)
    for task_id in move.paused:
        move.refs[task_id] = await latest_handoff(deps, task_id)


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
