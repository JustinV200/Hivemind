"""Order a quarantine: send `Intervene(QUARANTINE)` to the Warden of the bee's task.

The Queen (the Hive's orchestrator) never carries a quarantine out; ADR-0035's one code path lives
in the Warden (the supervisor of the bee's Cell), `hivemind.wardens.quarantine`. What she does is
order it (roadmap step 10.6c): on a Guard request, by rule for the dire patterns or by awake
decision otherwise, or by her own escalation policy (`QueenAction.QUARANTINE_BEE`, the row a
`PolicyAction.QUARANTINE` maps to). `order_quarantine` finds the Warden that holds the task in the
Brood Chamber (the task store), sends it the `hivemind.supervision.Quarantine` lever as a whole
`Intervene` (the bee, the task, the suspect episode and why), and records her decision as
`queen.decided`. `lever_from_alarm` reads a lever off an Alarm: its context names the task, the
bee and the trail event from which the bee's memory is suspect; an Alarm that names no task or no
event cannot scope a quarantine, and the caller escalates it to the human instead.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    quarantine sub-package. Called by `hivemind.queen.ticks.alarms` for a QUARANTINE_BEE decision
    about an Alarm, and by whoever else decides one from a Guard report (roadmap 10.6). Calls into
    `hivemind.brood_chamber` (TaskNotFoundError), `hivemind.queen.deps`, `hivemind.queen.trail`,
    `hivemind.supervision` (Quarantine, to_intervene) and waggle only.

Key invariants:
    - Nothing is sent for a task the chamber does not place on an attached Warden: the order would
      reach no one, so the caller hears False and escalates.
    - The wire message is built by `to_intervene`, never by hand: the lever's bee, task and suspect
      episode always travel together.

See Also:
    - hivemind.wardens.quarantine for the one code path this order reaches.
    - hivemind.queen.quarantine.hold for what the Queen does when the Warden reports the task held.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import TaskNotFoundError
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.trail import record_event
from hivemind.supervision import Quarantine, to_intervene
from waggle.envelope import wrap
from waggle.ids import AlarmId
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.supervision import AlarmRaised

__all__ = ["lever_from_alarm", "order_quarantine"]


async def order_quarantine(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    lever: Quarantine,
    alarm_id: AlarmId | None = None,
) -> bool:
    """Send `Intervene(QUARANTINE)` for `lever` to the Warden its task is placed on.

    Args:
        deps: The Queen's collaborators; the chamber says where the task is placed.
        wardens: Every Warden currently attached.
        lever: The bee, its task, the suspect episode and why; it must name its task, which is
            how the Queen finds the Warden that runs the bee.
        alarm_id: The Alarm this order answers, if any, so the trail links the two.

    Returns:
        True once the order is sent and `queen.decided` recorded; False when the lever names no
        task, or the task is unknown or not placed on an attached Warden (nothing was sent).
    """
    link = await _warden_of(deps, wardens, lever)
    if link is None or lever.task_id is None:
        return False
    await link.transport.send(
        wrap(to_intervene(lever, alarm_id=alarm_id), link.hop, clock=deps.clock)
    )
    await record_event(
        deps,
        "queen.decided",
        lever.task_id,
        action="QUARANTINE_BEE",
        warden_id=link.warden_id,
        suspect_episode_id=lever.suspect_episode_id,
    )
    return True


def lever_from_alarm(payload: AlarmRaised) -> Quarantine | None:
    """Read the Quarantine an Alarm asks for: its task, its bee and its suspect event.

    Args:
        payload: The escalated Alarm a QUARANTINE_BEE decision is about.

    Returns:
        The lever, suspect from the Alarm's own trail event; None when the Alarm names no task
        (no Warden to send it to) or no event (no episode to taint from).
    """
    context = payload.context
    if context.task_id is None or context.event_id is None:
        return None
    reason = f"Alarm {payload.alarm_id} ({payload.kind.value}): {payload.detail}"
    return Quarantine(
        reason=reason[:MAX_REASON_CHARS],
        bee=context.worker_id,
        task_id=context.task_id,
        suspect_episode_id=context.event_id,
    )


async def _warden_of(
    deps: QueenDeps, wardens: Sequence[WardenLink], lever: Quarantine
) -> WardenLink | None:
    """Return the attached Warden the lever's task is placed on, or None."""
    if lever.task_id is None:
        return None  # A bee alone does not say which Warden runs it; the chamber knows tasks.
    try:
        task = await deps.chamber.get(lever.task_id)
    except TaskNotFoundError:
        return None
    return next((link for link in wardens if link.warden_id == task.warden_id), None)
