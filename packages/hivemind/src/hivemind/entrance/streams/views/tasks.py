"""Stream task-graph deltas for one principal, the Queen or a Warden: ``/v1/tasks/stream``.

The Observation Hive's Attendant views show, for the Queen and for every Warden, the task graph
that principal is concerned with (roadmap 12.7), redrawn from deltas. Every task change is a
``task.*`` trail event recorded with it, so the view follows those through the stream hub
(ADR-0032) and sends each changed task, as ``GET /v1/tasks/{id}`` answers it (no words), in a
``TaskGraphFrame`` naming the principal. The Queen (``principal=queen``, the default) is
concerned with every task; a Warden with the tasks placed on it, and it also hears a task it was
shown leave it (unassigned, finished or cancelled), so its view never keeps a task it no longer
holds. A batch sends each task once, as it stands after the batch. It needs ``observe``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    row joins ``VIEWS``. Calls into the pump, the hub, the Brood Chamber (reads only) and the frame
    models.

Key invariants:
    - A frame never carries a task's words; a Warden's stream never shows another Warden's task
      it was not shown before.

See Also:
    - hivemind.entrance.routes.hive.tasks for the paged read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from starlette.websockets import WebSocket

from hivemind.brood_chamber import BroodChamber, Task, TaskNotFoundError
from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.socket import StreamContext, serve_socket
from hivemind.entrance.streams.views.pump import pump, trail_subscription
from hivemind.observation import TaskGraphFrame, task_view
from hivemind.pheromone import PheromoneEvent
from waggle.ids import TaskId

QUEEN_PRINCIPAL = "queen"  # The Queen's own graph: every task.
_PRINCIPAL_PATTERN = r"^(queen|warden_[0-9A-HJKMNP-TV-Z]{26})$"  # The Queen or one Warden.
_ACCESS = session_with("observe")  # A task's graph without its words is a read-only view.
_FAMILY = "task"  # The trail family every task change is recorded under.

__all__ = ["QUEEN_PRINCIPAL", "TASKS_VIEW", "task_stream"]


async def task_stream(
    websocket: WebSocket,
    services: Services,
    here: Here,
    principal: Annotated[
        str, Query(pattern=_PRINCIPAL_PATTERN, description="queen, or a Warden's id.")
    ] = QUEEN_PRINCIPAL,
) -> None:
    """Stream every change to one principal's task graph.

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
        principal: ``queen`` (every task) or a Warden's id (the tasks placed on it).
    """
    graph = _Graph(services.hive.chamber, principal)

    async def view(context: StreamContext) -> CloseReason:
        """Send each changed task in the principal's graph as it changes."""
        return await pump(context, trail_subscription(context, "tasks", _is_task), graph.frames)

    await serve_socket(websocket, _ACCESS, view, services, here)


class _Graph:
    """One principal's view of the task graph, remembering what a Warden's view was shown."""

    def __init__(self, chamber: BroodChamber, principal: str) -> None:
        """Start with nothing shown."""
        self._chamber = chamber
        self._principal = principal
        # The tasks a Warden's view is showing: it hears them leave even once unplaced.
        self._shown: set[TaskId] = set()

    async def frames(self, batch: tuple[PheromoneEvent, ...]) -> list[TaskGraphFrame]:
        """One frame per task the batch changed, in the principal's graph, as it stands now."""
        frames: list[TaskGraphFrame] = []
        # Each task once per batch, in the order its first change arrived.
        for task_id in dict.fromkeys(TaskId(event.subject_id) for event in batch):
            task = await self._read(task_id)
            if task is not None and self._concerns(task):
                frames.append(TaskGraphFrame(principal=self._principal, task=task_view(task)))
        return frames

    def _concerns(self, task: Task) -> bool:
        """Whether the task belongs in this graph now, or was shown and just left it."""
        if self._principal == QUEEN_PRINCIPAL:
            return True
        # A task placed here joins the Warden's view; one shown before is sent as it leaves.
        if task.warden_id == self._principal:
            self._shown.add(task.id)
            return True
        if task.id in self._shown:
            self._shown.discard(task.id)
            return True
        return False

    async def _read(self, task_id: TaskId) -> Task | None:
        """Read one task as it stands; None when it is not the chamber's (a stray event)."""
        try:
            # Latency: one local primary-key read of the Brood Chamber.
            return await self._chamber.get(task_id)
        except TaskNotFoundError:
            return None


def _is_task(event: PheromoneEvent) -> bool:
    """Accept every task change."""
    return event.family == _FAMILY


TASKS_VIEW = SocketSpec(
    path="/v1/tasks/stream",
    listeners=BOTH_LISTENERS,
    access=_ACCESS,
    endpoint=task_stream,
    summary="Task-graph deltas for the Queen (every task) or one Warden (its tasks).",
    frame_model=TaskGraphFrame,
)
