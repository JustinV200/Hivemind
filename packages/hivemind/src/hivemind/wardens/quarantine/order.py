"""Define QuarantineOrder: one order to quarantine a sub-bee, and the three ways a Warden gets one.

A quarantine (roadmap step 10.6c, ADR-0035) is one intervention with one code path
(`hivemind.wardens.quarantine.path`), but three things can ask a Warden (the supervisor of one
Cell) for it: the Queen (the Hive's orchestrator), with a `waggle.messages.supervision.Intervene`
whose action is `QUARANTINE`; this Warden's own escalation policy, when a row names
`PolicyAction.QUARANTINE` for an Alarm one of its sub-bees raised ("a Warden may apply it to its
own sub-bee by its own policy row, since it may already cancel one"); and a direct
`Warden.intervene(child, Quarantine(...))`, the `Supervisor` protocol's own lever. Each becomes the
same `QuarantineOrder`: the `hivemind.supervision.Quarantine` lever (the bee, its task, the
episode from which its memory is suspect, and why), who ordered it, what that principal holds (the
`quarantine` enforcement point checks it) and the Alarm the order answers, if any. Nothing here
acts; building an order is pure.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Built by `hivemind.wardens.quarantine.path`'s entry points from what
    `hivemind.wardens.warden.Warden`'s tick decided; read by the path, its `authority` check and
    its `report`. Calls into `hivemind.guard` (principals, role sets), `hivemind.supervision`
    (Quarantine, from_wire) and waggle only.

Key invariants:
    - An order always carries a full `Quarantine` lever: a suspect episode, and a bee or a task.
    - The Queen's order is read through `hivemind.supervision.from_wire`, so a wire QUARANTINE
      with no suspect episode is refused there, never guessed at here.
    - An order from this Warden's own policy names the sub-bee the Alarm is about, and takes its
      memory to be suspect from the Alarm's own event, or else from that bee's `worker.spawned`.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md, "Quarantine is
      one intervention".
    - hivemind.wardens.quarantine.path for the one code path every order goes through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.guard import (
    QUEEN_ROLE,
    CapabilitySet,
    PrincipalRef,
    queen_principal,
    role_set,
    warden_principal,
)
from hivemind.supervision import Quarantine, SupervisionError, from_wire
from waggle.ids import AlarmId, WorkerId
from waggle.messages.supervision import AlarmRaised, Intervene

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

__all__ = ["QuarantineOrder", "lever_order", "own_policy_order", "queen_order"]


@dataclass(frozen=True, slots=True)
class QuarantineOrder:
    """One order to quarantine a sub-bee: the lever, who gave it, what they hold, and why now."""

    lever: Quarantine  # The bee, its task, the suspect episode and the reason.
    ordered_by: PrincipalRef  # The Queen, or this Warden itself.
    held: CapabilitySet  # What the orderer holds; the `quarantine` point checks it.
    alarm_id: AlarmId | None = None  # The Alarm this order answers, so the trail links the two.


def queen_order(warden: Warden, intervene: Intervene) -> QuarantineOrder:
    """Read the Queen's `Intervene(QUARANTINE)` as an order.

    Args:
        warden: The Warden that received it; its Guard policy says what the Queen holds.
        intervene: The wire order, already validated (a suspect episode and a bee or task).

    Returns:
        The order, from the Queen, holding her role's default set.

    Raises:
        SupervisionError: `intervene` is not a QUARANTINE (the caller dispatched it wrongly), or
            it carries no suspect episode (a peer that skipped validation).
    """
    lever = from_wire(intervene)
    # The autopilot table routes only QUARANTINE here; anything else is a dispatch bug, and
    # carrying it out as a quarantine would do something nobody ordered.
    if not isinstance(lever, Quarantine):
        raise SupervisionError(f"Intervene {intervene.action.value} is not a quarantine order.")
    deps = warden._deps
    return QuarantineOrder(
        lever=lever,
        ordered_by=queen_principal(deps.identity.hive_id),
        held=role_set(deps.guard, QUEEN_ROLE),
        alarm_id=intervene.alarm_id,
    )


def own_policy_order(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised) -> QuarantineOrder:
    """Build the order this Warden's own policy row gives for `alarm` about `sub_bee`.

    Args:
        warden: This Warden; it orders the quarantine and holds its own capability set.
        sub_bee: The sub-bee the Alarm is about.
        alarm: The Alarm whose policy row named QUARANTINE.

    Returns:
        The order, suspect from the Alarm's own event or else from the bee's `worker.spawned`.

    Raises:
        SupervisionError: Neither the Alarm nor the sub-bee names an event to start from (a
            sub-bee row built outside `spawn_sub_bee`).
    """
    episode = alarm.context.event_id or sub_bee.spawned_event_id
    # Without a starting point the taint would have no "from then on"; guessing one could leave
    # suspect memory unlabelled, so the order is refused instead.
    if episode is None:
        raise SupervisionError(
            f"Alarm {alarm.alarm_id} about {sub_bee.worker_id} names no event to quarantine from."
        )
    lever = Quarantine(
        reason=f"Alarm {alarm.alarm_id} ({alarm.kind.value}) matched a QUARANTINE policy row.",
        bee=sub_bee.worker_id,
        task_id=sub_bee.task_id,
        suspect_episode_id=episode,
    )
    return _warden_order(warden, lever, alarm.alarm_id)


def lever_order(warden: Warden, child: WorkerId, lever: Quarantine) -> QuarantineOrder:
    """Build the order `Warden.intervene(child, lever)` gives: this Warden's own lever.

    Args:
        warden: This Warden, the orderer.
        child: The sub-bee `Supervisor.intervene` addressed.
        lever: The Quarantine it was handed; its own bee, when set, must be `child`.

    Returns:
        The order, naming `child` as the bee.

    Raises:
        SupervisionError: `lever` names a different bee than `child`.
    """
    # Two different bees named at once is ambiguous; neither reading can be assumed.
    if lever.bee is not None and lever.bee != child:
        raise SupervisionError(f"Quarantine names {lever.bee} but was addressed to {child}.")
    return _warden_order(warden, lever.model_copy(update={"bee": child}), None)


def _warden_order(warden: Warden, lever: Quarantine, alarm_id: AlarmId | None) -> QuarantineOrder:
    """Wrap `lever` as an order from this Warden itself, holding its own capability set."""
    return QuarantineOrder(
        lever=lever,
        ordered_by=warden_principal(warden._warden_id),
        held=warden._ceiling,
        alarm_id=alarm_id,
    )
