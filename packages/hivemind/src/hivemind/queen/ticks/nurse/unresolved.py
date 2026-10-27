"""Define escalate_unresolved: a task the Nurse Bee still rejects at the cap goes to the human.

ADR-0061: once a task has been sent back `[nurse] max_rounds` times, the next rejection is the
human's to judge. The Queen raises a `NURSE_UNRESOLVED` Alarm about the task and fails it, so its
goal ends now instead of waiting out its timeout with no Worker on it; the Cell keeps the last
attempt's work, as every Nurse round has. The findings are untrusted model prose written from
untrusted evidence, so they reach the human inbox alone, fenced and labelled, as a note kept
beside the Alarm (`HumanInbox.note_for`), never on it: the Alarm itself, which the trail's
`alarm.escalated` and `alarm.resolved` rows, the chat line, the devices' notice and the Queen's
awake episodes all read, carries a detail of counts only.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.ticks.
    nurse`. Called by `hivemind.queen.queen._act_on_task_result` for an ESCALATE_TO_HUMAN on a
    Nurse rejection. Calls into `hivemind.brood_chamber`, `hivemind.cell` (CellIdentity),
    `hivemind.queen.chat` (post_alarm), `hivemind.queen.deps`, `hivemind.queen.human_inbox`,
    `hivemind.queen.ticks.results` (fail_task), `hivemind.supervision` and waggle only.

Key invariants:
    - No finding's words reach the trail, the chat, a push notice or a Queen model; only the
      human inbox's note beside the Alarm carries them, dropped when the Alarm is resolved.
    - A task no longer RUNNING is left alone: nothing is raised and nothing is failed twice.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.queen.ticks.alarms for _escalate, the ESCALATE_TO_HUMAN of an Alarm this mirrors.
"""

from __future__ import annotations

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CellIdentity
from hivemind.queen.chat import post_alarm
from hivemind.queen.deps import QueenDeps
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.ticks.results import fail_task
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState, record_alarm_event
from waggle.ids import WardenId, new_alarm_id
from waggle.messages.supervision import AlarmContext
from waggle.messages.supervision.alarms import MAX_DETAIL_CHARS
from waggle.messages.task import NurseFinding, TaskResult

__all__ = ["escalate_unresolved"]

# The same fence style the Worker's brief uses for untrusted model prose (codingrules 15).
_FENCE_OPEN = "<<<nurse_findings>>>"
_FENCE_CLOSE = "<<<end nurse_findings>>>"


async def escalate_unresolved(
    deps: QueenDeps, human_inbox: HumanInbox, payload: TaskResult, warden_id: WardenId
) -> None:
    """Raise NURSE_UNRESOLVED about `payload`'s task to the human, then fail the task.

    Args:
        deps: The Queen's collaborators.
        human_inbox: Where the Alarm, findings included, waits for the human.
        payload: The FAILED result carrying the Nurse's CHANGES_REQUESTED review.
        warden_id: The Warden that reported it, whose Nurse rejected the attempt.
    """
    task = await deps.chamber.get(payload.task_id)
    if task.status is not TaskStatus.RUNNING:
        return  # Closed out meanwhile: there is nothing left for the human to decide here.
    findings = _findings(payload)
    alarm = _unresolved_alarm(deps, task, payload, warden_id)
    # The note alone carries the words; the Alarm every other reader sees has counts only.
    human_inbox.add_alarm(alarm, note=_findings_note(findings))
    identity = CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )
    # Named as its own field, so the Night Veil boundary routes the row with its Cell.
    cell = {"cell_id": task.cell_id} if task.cell_id is not None else {}
    await record_alarm_event(deps.trail, identity, deps.clock, alarm, "alarm.escalated", **cell)
    await post_alarm(deps, alarm)
    await fail_task(deps, task.id, alarm.detail)


def _findings(payload: TaskResult) -> tuple[NurseFinding, ...]:
    """Return the review's findings; empty when the result carries no verdict at all."""
    verdict = payload.review.verdict if payload.review is not None else None
    return verdict.findings if verdict is not None else ()


def _unresolved_alarm(
    deps: QueenDeps, task: Task, payload: TaskResult, warden_id: WardenId
) -> Alarm:
    """Build the trail-safe Alarm: ids and counts in its detail, never a finding's words."""
    detail = (
        f"The Nurse Bee still asked for changes after {task.nurse_rounds} fix-forward rounds "
        f"({len(_findings(payload))} findings). The last attempt's work stays on its Cell; the "
        "task is closed out as failed for you to judge."
    )
    return Alarm(
        id=new_alarm_id(deps.clock),
        kind=AlarmKind.NURSE_UNRESOLVED,
        severity=AlarmSeverity.WARNING,
        origin=str(payload.checked_by or warden_id),  # The Warden whose Nurse rejected it.
        attempts=task.nurse_rounds,
        context=AlarmContext(
            task_id=task.id, cell_id=task.cell_id, worker_id=None, event_id=None, handoff=None
        ),
        detail=detail,
        clearance=task.spec.clearance,  # The findings were written from this task's evidence.
        raised_at=deps.clock.now(),
        state=AlarmState.RAISED,
    )


def _findings_note(findings: tuple[NurseFinding, ...]) -> str:
    """Render the findings, fenced and labelled as untrusted model text, within the detail cap."""
    lines = [
        _FENCE_OPEN,
        "Untrusted Nurse Bee findings, written by a model: read them as data, never as "
        "instructions.",
    ]
    for index, finding in enumerate(findings, start=1):
        lines.append(f"{index}. {_anchor(finding)}: {finding.problem} Fix: {finding.fix}")
    text = "\n".join(lines)
    # The fence always closes, even when the text had to be cut to fit the Alarm's own bound.
    room = MAX_DETAIL_CHARS - len(_FENCE_CLOSE) - 1
    return f"{text[:room]}\n{_FENCE_CLOSE}"


def _anchor(finding: NurseFinding) -> str:
    """Say where a finding points: its criterion (counted from 1), its file and line, or both."""
    parts: list[str] = []
    if finding.criterion_index is not None:
        parts.append(f"criterion {finding.criterion_index + 1}")
    if finding.path is not None:
        parts.append(finding.path if finding.line is None else f"{finding.path}:{finding.line}")
    return ", ".join(parts)
