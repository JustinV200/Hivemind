"""Define the frames the Hive's live views send: trail, telemetry, Forage, tasks, episodes, Cells.

Clients never poll (codingrules 8.11): each Observation Hive view subscribes to one WebSocket
stream, and each stream sends JSON text frames of one model, tagged by ``type`` (ADR-0040), so a
client parses a stream from the published contract alone. A frame carries the same read model the
matching route answers with: a trail event, a task, an episode record, a Cell. Two frames carry
more than one: a Forage delta is the ``forage.*`` event that moved the ledger, the grant it named
as the ledger now holds it (null once it is gone) and the headroom after it; a telemetry sample is
one bee's self-report from a Heartbeat (the Warden's own, or one sub-bee's row), whose goal line,
last actions and blockers are the bee's own words, so its stream needs ``observe:thoughts`` and
``honey:clearance:c2``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views``. Sent by
    the views in ``hivemind.entrance.streams.views``; published in the OpenAPI document's
    ``x-hive-streams``. Calls into the view models beside it, the wire telemetry models and
    pydantic.

Key invariants:
    - Every frame's ``type`` is a literal naming its stream; a frame carries a route's view.

See Also:
    - hivemind.entrance.streams.views for the streams that send them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.observation.views.cells import CellView
from hivemind.observation.views.episodes import EpisodeView
from hivemind.observation.views.forage import GrantView, HeadroomView
from hivemind.observation.views.tasks import TaskView
from hivemind.observation.views.trail import TrailEventView
from waggle.messages.base import TaskIdField, WardenIdField, WorkerIdField
from waggle.messages.supervision import ContextTelemetry, Heartbeat, WardenState, WorkerState

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every frame here: immutable, no strays.

__all__ = [
    "CellFrame",
    "EpisodeFrame",
    "ForageFrame",
    "TaskGraphFrame",
    "TelemetryFrame",
    "TelemetrySampleView",
    "TrailFrame",
    "telemetry_samples",
]


class TrailFrame(BaseModel):
    """One trail event, as ``/v1/trail/stream`` sends it."""

    model_config = _CONFIG

    type: Literal["trail"] = Field(default="trail", description="Always trail.")
    event: TrailEventView = Field(description="The event.")


class TelemetrySampleView(BaseModel):
    """One bee's self-report from a Heartbeat: its context, goal line and spend (C2)."""

    model_config = _CONFIG

    warden_id: WardenIdField = Field(description="The Warden whose Heartbeat carried it.")
    worker_id: WorkerIdField | None = Field(
        description="The sub-bee it describes; null for the Warden's own report."
    )
    task_id: TaskIdField | None = Field(description="The bee's current task, if any.")
    warden_state: WardenState | None = Field(description="The Warden's state (its own report).")
    worker_state: WorkerState | None = Field(description="The sub-bee's state (its row).")
    tokens_used: int = Field(description="Tokens in the bee's context.")
    context_window: int = Field(description="The bound model's context window.")
    goal: str = Field(description="The bee's current goal, in one line (its own words).")
    last_actions: list[str] = Field(description="Its most recent actions.")
    blockers: list[str] = Field(description="What it is stuck on.")
    spend: float = Field(description="Its spend so far.")
    at: datetime = Field(description="When the Queen received the Heartbeat.")


class TelemetryFrame(BaseModel):
    """One telemetry sample, as ``/v1/telemetry/stream`` sends it (C2)."""

    model_config = _CONFIG

    type: Literal["telemetry"] = Field(default="telemetry", description="Always telemetry.")
    sample: TelemetrySampleView = Field(description="The sample.")


class ForageFrame(BaseModel):
    """One Forage ledger delta, as ``/v1/forage/stream`` sends it."""

    model_config = _CONFIG

    type: Literal["forage"] = Field(default="forage", description="Always forage.")
    event: TrailEventView = Field(description="The forage.* event that moved the ledger.")
    grant: GrantView | None = Field(
        description="The grant it named, as the ledger holds it now; null when it names none "
        "or the grant is gone (revoked or expired)."
    )
    headroom: HeadroomView = Field(description="The shared pool's headroom after it.")


class TaskGraphFrame(BaseModel):
    """One task that changed, in one principal's task graph (``/v1/tasks/stream``)."""

    model_config = _CONFIG

    type: Literal["task"] = Field(default="task", description="Always task.")
    principal: str = Field(description="Whose graph: queen, or a Warden's id.")
    task: TaskView = Field(description="The task as it stands now, without its words.")


class EpisodeFrame(BaseModel):
    """One new episode record, as ``/v1/episodes/stream`` sends it (C2)."""

    model_config = _CONFIG

    type: Literal["episode"] = Field(default="episode", description="Always episode.")
    episode: EpisodeView = Field(description="The record.")


class CellFrame(BaseModel):
    """One Cell whose status changed (or every Cell, first), from ``/v1/cells/stream``."""

    model_config = _CONFIG

    type: Literal["cell"] = Field(default="cell", description="Always cell.")
    cell: CellView = Field(description="The Cell as it stands now.")


def telemetry_samples(
    warden_id: str, heartbeat: Heartbeat, at: datetime
) -> list[TelemetrySampleView]:
    """Split one Heartbeat into a sample per bee: the Warden's own, then each sub-bee's row.

    Args:
        warden_id: The Warden that sent it.
        heartbeat: The Heartbeat, as the Queen received it.
        at: When she received it.

    Returns:
        One sample for the Warden, then one per sub-bee row, in the Heartbeat's order.
    """
    samples = [
        _sample(warden_id, None, heartbeat.task_id, heartbeat.telemetry, at).model_copy(
            update={"warden_state": heartbeat.warden_state}
        )
    ]
    # Each sub-bee's row carries its own state, task and telemetry.
    for child in heartbeat.children:
        sample = _sample(warden_id, child.worker_id, child.task_id, child.telemetry, at)
        samples.append(sample.model_copy(update={"worker_state": child.state}))
    return samples


def _sample(
    warden_id: str,
    worker_id: str | None,
    task_id: str | None,
    telemetry: ContextTelemetry,
    at: datetime,
) -> TelemetrySampleView:
    """Build one sample from a bee's telemetry, its states left for the caller to set."""
    return TelemetrySampleView(
        warden_id=warden_id,
        worker_id=worker_id,
        task_id=task_id,
        warden_state=None,
        worker_state=None,
        tokens_used=telemetry.tokens_used,
        context_window=telemetry.context_window,
        goal=telemetry.goal,
        last_actions=list(telemetry.last_actions),
        blockers=list(telemetry.blockers),
        spend=telemetry.spend,
        at=at,
    )
