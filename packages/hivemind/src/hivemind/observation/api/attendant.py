"""Build an Attendant view: the inbox behind a supervisor, the task graph it watches, what is next.

Every supervisor orders its inbox with an Attendant (codingrules 8.8), and the Observation Hive's
Attendant views (roadmap 12.7) show that ordering for the Queen and for every Warden beside the task
graph the principal is concerned with: its tasks as nodes with status, placement and open Alarms,
the dependencies between them as edges, what finished recently and what is coming next. The live
inbox lives in each supervisor's memory for the length of a tick, so no store holds it. This view
therefore reconstructs it from the durable things that wait on a supervisor (an open Alarm, a task
blocked on a question, a human message not yet handled) and scores each with the same
``score_item`` and weight table the Attendant uses, so the order and the numbers are the
Attendant's own. It says so in ``inbox_is_reconstructed``; items that exist only in memory (a
heartbeat, a timer, a Guard request) are not listed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built from
    the Brood Chamber, the trail and the chat log by ``hivemind.entrance.reads.screens.attendant``.
    Pure: calls into the Attendant's scoring core, the task record and pydantic.

Key invariants:
    - Pure: tasks, Alarms and waiting messages are handed in; time is an argument.
    - No inbox entry carries words: ids, kinds, scores and the scorer's own reason codes.
    - Edges only join tasks that are both nodes; a node is never listed twice.

See Also:
    - hivemind.supervision.attendant for the scorer this view reuses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import Task, TaskStatus, is_terminal
from hivemind.observation.api.alarms import OpenAlarmView
from hivemind.supervision import AlarmSeverity
from hivemind.supervision.attendant import InboxItem, InboxKind, WeightTable, score_item
from waggle.messages.base import CellIdField, TaskIdField, WardenIdField
from waggle.messages.task import WorkerRole

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

QUEEN_PRINCIPAL = "queen"  # The Queen's own principal in the Attendant routes.
HUMAN_PRINCIPAL = "human"  # What a waiting human message is scored under.
RECENT_FINISHED = 10  # Finished tasks listed: a glance back, not a history.
UP_NEXT = 10  # Tasks listed as coming next.

__all__ = [
    "HUMAN_PRINCIPAL",
    "QUEEN_PRINCIPAL",
    "RECENT_FINISHED",
    "UP_NEXT",
    "AttendantInputs",
    "AttendantView",
    "InboxEntryView",
    "TaskEdge",
    "TaskNode",
    "attendant_view",
    "reconstruct_inbox",
]


class InboxEntryView(BaseModel):
    """One item in a supervisor's ordered inbox, with the score the Attendant gave it."""

    model_config = _CONFIG

    id: str = Field(description="The item's id: an Alarm, a question or a chat entry.")
    kind: InboxKind = Field(description="ALARM, QUESTION or HUMAN_MESSAGE as listed here.")
    score: float = Field(description="The Attendant's score; higher sorts first.")
    reasons: list[str] = Field(description="The scorer's factor-by-factor reasons.")
    task_id: TaskIdField | None = Field(description="The task it concerns, if any.")
    severity: AlarmSeverity | None = Field(description="An Alarm's severity; null otherwise.")
    received_at: datetime = Field(description="When it began waiting.")


class TaskNode(BaseModel):
    """One task in the graph a principal watches."""

    model_config = _CONFIG

    task_id: TaskIdField = Field(description="The task.")
    status: TaskStatus = Field(description="Where it stands.")
    role: WorkerRole = Field(description="The Worker role its Warden spawns for it.")
    cell_id: CellIdField | None = Field(description="Its placement Cell; null while unplaced.")
    warden_id: WardenIdField | None = Field(description="Its Warden; null while unplaced.")
    open_alarms: int = Field(description="Open Alarms that concern it.")


class TaskEdge(BaseModel):
    """A dependency: ``after`` starts only once ``before`` has succeeded."""

    model_config = _CONFIG

    before: TaskIdField = Field(description="The task depended on.")
    after: TaskIdField = Field(description="The task that waits.")


class AttendantView(BaseModel):
    """The Attendant view for the Queen or one Warden (observe)."""

    model_config = _CONFIG

    principal: str = Field(description="queen, or a Warden's id.")
    as_of: datetime = Field(description="When the view was built.")
    inbox_is_reconstructed: bool = Field(
        description="True: the inbox is rebuilt from durable state and scored with the "
        "Attendant's own scorer; the supervisor's in-memory tick queue is not readable."
    )
    inbox: list[InboxEntryView] = Field(description="The ordered inbox, highest score first.")
    nodes: list[TaskNode] = Field(description="The tasks it is concerned with.")
    edges: list[TaskEdge] = Field(description="Dependencies between those tasks.")
    recently_finished: list[TaskIdField] = Field(description="Newest finished first.")
    up_next: list[TaskIdField] = Field(description="Ready or assigned tasks, oldest first.")


@dataclass(frozen=True, slots=True)
class AttendantInputs:
    """The gathered records an Attendant view is built from.

    Attributes:
        principal: ``queen`` or a Warden's id.
        tasks: Every unfinished task plus recently finished ones.
        alarms: The open Alarms.
        waiting_messages: Human messages not yet handled, as (entry id, when it arrived); the
            Queen's inbox only.
        as_of: The instant the view is built for.
    """

    principal: str
    tasks: Sequence[Task]
    alarms: Sequence[OpenAlarmView]
    waiting_messages: Sequence[tuple[str, datetime]]
    as_of: datetime


def attendant_view(inputs: AttendantInputs) -> AttendantView:
    """Build the Attendant view for the principal in ``inputs``.

    Args:
        inputs: The gathered records.

    Returns:
        The reconstructed, scored inbox, the task graph, what finished and what is next.
    """
    queen = inputs.principal == QUEEN_PRINCIPAL
    tasks = [task for task in inputs.tasks if queen or _concerns_warden(task, inputs.principal)]
    weights = WeightTable.queen_default() if queen else WeightTable.warden_default()
    items = reconstruct_inbox(inputs, tasks)
    return AttendantView(
        principal=inputs.principal,
        as_of=inputs.as_of,
        inbox_is_reconstructed=True,
        inbox=_ordered(items, weights, inputs.as_of),
        nodes=_nodes(tasks, inputs.alarms),
        edges=_edges(tasks),
        recently_finished=_recent(tasks),
        up_next=_up_next(tasks, queen),
    )


def reconstruct_inbox(inputs: AttendantInputs, tasks: Sequence[Task]) -> list[InboxItem]:
    """Rebuild the durable items waiting on a supervisor as inbox items.

    Args:
        inputs: The gathered records (for Alarms, waiting messages and the principal).
        tasks: The principal's tasks.

    Returns:
        Open Alarms on its tasks or Cells, a question per blocked task, and for the Queen the
        human messages still waiting.
    """
    queen = inputs.principal == QUEEN_PRINCIPAL
    mine = {task.id for task in tasks}
    cells = {task.cell_id for task in tasks if task.cell_id is not None}
    items: list[InboxItem] = []
    # An Alarm is the Queen's to see; a Warden sees those on its tasks or Cells.
    for alarm in inputs.alarms:
        if queen or alarm.task_id in mine or alarm.cell_id in cells:
            items.append(_alarm_item(alarm))
    # A blocked task is a question waiting on an answer.
    for task in tasks:
        if task.status is TaskStatus.BLOCKED and task.pending_question_id is not None:
            items.append(_question_item(task, inputs.principal))
    # The human talks to the Queen alone.
    if queen:
        items.extend(_message_item(entry, at) for entry, at in inputs.waiting_messages)
    return items


def _ordered(
    items: Sequence[InboxItem], weights: WeightTable, now: datetime
) -> list[InboxEntryView]:
    """Score items and order them as the Attendant does: score, then age, then id."""
    scored = [
        (item, score_item(weights, item, now)) for item in items if not weights.refuses(item.kind)
    ]
    scored.sort(key=lambda pair: (-pair[1].score, pair[0].received_at, pair[0].id))
    return [
        InboxEntryView(
            id=item.id,
            kind=item.kind,
            score=priority.score,
            reasons=list(priority.reasons),
            task_id=item.task_id,
            severity=item.severity,
            received_at=item.received_at,
        )
        for item, priority in scored
    ]


def _alarm_item(alarm: OpenAlarmView) -> InboxItem:
    """Wrap an open Alarm as the Attendant sees one."""
    return InboxItem(
        id=alarm.id,
        kind=InboxKind.ALARM,
        received_at=alarm.raised_at,
        principal="system",
        severity=alarm.severity,
        task_id=alarm.task_id,
        payload_kind="alarm.raised",
        payload=alarm.id,
    )


def _question_item(task: Task, principal: str) -> InboxItem:
    """Wrap a blocked task's pending question."""
    return InboxItem(
        id=str(task.pending_question_id),
        kind=InboxKind.QUESTION,
        received_at=task.updated_at,
        principal=task.warden_id or principal,
        severity=None,
        task_id=task.id,
        payload_kind="supervision.question",
        payload=task.pending_question_id,
    )


def _message_item(entry_id: str, at: datetime) -> InboxItem:
    """Wrap a human message still waiting for the Queen."""
    return InboxItem(
        id=entry_id,
        kind=InboxKind.HUMAN_MESSAGE,
        received_at=at,
        principal=HUMAN_PRINCIPAL,
        severity=None,
        task_id=None,
        payload_kind="control.human_message",
        payload=entry_id,
    )


def _concerns_warden(task: Task, warden_id: str) -> bool:
    """Whether a task is in a Warden's graph: placed on it, or verified by it when finished."""
    verified = task.outcome.verified_by if task.outcome is not None else None
    return task.warden_id == warden_id or verified == warden_id


def _nodes(tasks: Sequence[Task], alarms: Sequence[OpenAlarmView]) -> list[TaskNode]:
    """List the tasks as graph nodes, oldest first, each with its open Alarm count."""
    ordered = sorted(tasks, key=lambda task: (task.created_at, task.id))
    return [
        TaskNode(
            task_id=task.id,
            status=task.status,
            role=task.spec.role,
            cell_id=task.cell_id,
            warden_id=task.warden_id,
            open_alarms=sum(1 for alarm in alarms if alarm.task_id == task.id),
        )
        for task in ordered
    ]


def _edges(tasks: Sequence[Task]) -> list[TaskEdge]:
    """List the dependencies whose both ends are in the graph."""
    ids = {task.id for task in tasks}
    return [
        TaskEdge(before=dependency, after=task.id)
        for task in sorted(tasks, key=lambda task: (task.created_at, task.id))
        for dependency in task.spec.depends_on
        if dependency in ids
    ]


def _recent(tasks: Sequence[Task]) -> list[str]:
    """List the newest finished tasks, newest first."""
    finished = [task for task in tasks if is_terminal(task.status)]
    finished.sort(key=lambda task: (task.updated_at, task.id), reverse=True)
    return [task.id for task in finished[:RECENT_FINISHED]]


def _up_next(tasks: Sequence[Task], queen: bool) -> list[str]:
    """List what is coming: the Queen's ready PENDING tasks, a Warden's ASSIGNED ones."""
    unfinished = {task.id for task in tasks if not is_terminal(task.status)}
    wanted = TaskStatus.PENDING if queen else TaskStatus.ASSIGNED
    ready = [
        task
        for task in tasks
        if task.status is wanted and not unfinished.intersection(task.spec.depends_on)
    ]
    ready.sort(key=lambda task: (task.created_at, task.id))
    return [task.id for task in ready[:UP_NEXT]]
