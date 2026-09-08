"""Define what a bee reports about itself: its state, its context telemetry, its compacted view.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and its
supervision family is the one ``Supervisor`` protocol at every level of the tree (human, Queen,
Wardens, sub-bees). The value models here are the bounded self-description every level sends up:
``ContextTelemetry`` (what every bee reports on heartbeat, bounded so it can never smuggle a
transcript), ``ChildTelemetry`` (one sub-bee's row in a Warden's aggregate heartbeat; a Warden is
the always-on supervisor of one Cell, a unit of compute) and ``CompactView`` (the compacted view an
inspection returns, shaped after the sections of a Handoff, the document a bee writes before its
context is reset). ``WorkerState`` and ``WardenState`` are the wire forms of the two bee state
machines that ride on those rows. The messages that carry them (heartbeat, inspect and its reply,
intervene) live in ``waggle.messages.supervision.oversight``; the models are split out here by
responsibility so each file stays under the codingrules 5.1 size limit. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.supervision.oversight, whose
    Heartbeat and InspectReply carry these models; mirrored by hivemind.supervision
    (ContextTelemetry) and the state machines of hivemind.workers and hivemind.wardens; calls
    into waggle.messages.base only.

Key invariants:
    - Every model is frozen and forbids extras through VALUE_MODEL_CONFIG, like a message, but
      none subclasses WaggleMessage, so none can ever be registered as a kind.
    - Every text field is bounded, and a CompactView is bounded in total (validator), so no
      telemetry row or view can carry a transcript up the tree.

See Also:
    - docs/waggle/spec.md section 8.3 for the normative fields, bounds and validators.
    - waggle.messages.supervision.oversight for Heartbeat, Inspect, InspectReply and Intervene.
    - waggle.messages.base for VALUE_MODEL_CONFIG and the id aliases.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import VALUE_MODEL_CONFIG, TaskIdField, WorkerIdField

MIN_TOKENS_USED = 0  # A context holds no fewer than no tokens.
MIN_CONTEXT_WINDOW = 1  # Greater than 0: a model with no window could hold no context at all.
MAX_GOAL_CHARS = 1_000  # The current goal in one line; the objective itself rides on task.assign.
MAX_LAST_ACTIONS = 10  # Enough recent actions to see what the bee is doing, never its history.
MAX_ACTION_CHARS = 200  # One action is a sentence: a tool call, a file touched, a check run.
MAX_BLOCKERS = 10  # More open blockers than this is itself the blocker; raise an Alarm instead.
MAX_BLOCKER_CHARS = 500  # A blocker is a short paragraph: what is stuck and on what.
MIN_SPEND = 0.0  # Spend is never negative; the Hive meters cost, never refunds.
MAX_PROGRESS_CHARS = 2_000  # A view's progress section is a paragraph, as a Handoff's is.
MAX_VIEW_ITEMS = 16  # Decisions or open threads: a Handoff lists a handful, not a log.
MAX_VIEW_ITEM_CHARS = 500  # One decision or one open thread is a short paragraph.
MAX_VIEW_CHARS = 8_000  # The whole view: the most an inspect may ask for, and a few screens.

__all__ = [
    "MAX_ACTION_CHARS",
    "MAX_BLOCKERS",
    "MAX_BLOCKER_CHARS",
    "MAX_GOAL_CHARS",
    "MAX_LAST_ACTIONS",
    "MAX_PROGRESS_CHARS",
    "MAX_VIEW_CHARS",
    "MAX_VIEW_ITEMS",
    "MAX_VIEW_ITEM_CHARS",
    "MIN_CONTEXT_WINDOW",
    "MIN_SPEND",
    "MIN_TOKENS_USED",
    "ChildTelemetry",
    "CompactView",
    "ContextTelemetry",
    "WardenState",
    "WorkerState",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class WorkerState(Enum):
    """Where a Worker is in its life, as its heartbeat and its Warden's rows report it."""

    SPAWNED = "SPAWNED"
    RUNNING = "RUNNING"
    HANDING_OFF = "HANDING_OFF"  # Writing its Handoff before a reset, rebind or takeover.
    PAUSED = "PAUSED"
    DONE = "DONE"
    FAILED = "FAILED"
    KILLED = "KILLED"


class WardenState(Enum):
    """Where a Warden is in its life, as its heartbeat reports it."""

    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    WATCH = "WATCH"  # A Real Cell's Warden with no active bees, observing read-only.
    OFFLINE = "OFFLINE"  # Cut off from the Queen, running on its outbox and local Forage.
    CLUSTERED = "CLUSTERED"  # Paused and preserved while a model provider is down.
    MIGRATING = "MIGRATING"
    STOPPED = "STOPPED"


# ──────────────────────────────────────────────────────────────────────────────
# Value models
# ──────────────────────────────────────────────────────────────────────────────


class ContextTelemetry(BaseModel):
    """What every bee reports on heartbeat, bounded so it can never smuggle a transcript.

    Carried by supervision.heartbeat (the sender's own, and one per sub-bee row) and by
    supervision.inspect_reply; mirrored by hivemind.supervision.ContextTelemetry.
    """

    model_config = VALUE_MODEL_CONFIG

    tokens_used: int = Field(ge=MIN_TOKENS_USED, description="Tokens in the bee's context.")
    context_window: int = Field(ge=MIN_CONTEXT_WINDOW, description="The bound model's window.")
    goal: str = Field(max_length=MAX_GOAL_CHARS, description="The current goal in one line.")
    last_actions: tuple[Annotated[str, Field(max_length=MAX_ACTION_CHARS)], ...] = Field(
        max_length=MAX_LAST_ACTIONS, description="The most recent actions."
    )
    blockers: tuple[Annotated[str, Field(max_length=MAX_BLOCKER_CHARS)], ...] = Field(
        max_length=MAX_BLOCKERS, description="What the bee is stuck on."
    )
    spend: float = Field(ge=MIN_SPEND, description="Spend so far.")


class ChildTelemetry(BaseModel):
    """One sub-bee's row in a Warden's aggregate heartbeat."""

    model_config = VALUE_MODEL_CONFIG

    worker_id: WorkerIdField = Field(description="The sub-bee this row describes.")
    task_id: TaskIdField | None = Field(
        description="The sub-bee's current task; None while it has none."
    )
    state: WorkerState = Field(description="Where the sub-bee is in its life.")
    telemetry: ContextTelemetry = Field(description="The sub-bee's own telemetry.")


class CompactView(BaseModel):
    """The compacted view an inspection returns, shaped after a Handoff's sections.

    Carried by supervision.inspect_reply; bounded in total so a view can never be a transcript,
    and the receiver further holds it to the cap its Inspect asked for.
    """

    model_config = VALUE_MODEL_CONFIG

    goal: str = Field(max_length=MAX_GOAL_CHARS, description="The current goal in one line.")
    progress: str = Field(
        max_length=MAX_PROGRESS_CHARS, description="What has been done so far, in a paragraph."
    )
    decisions: tuple[Annotated[str, Field(max_length=MAX_VIEW_ITEM_CHARS)], ...] = Field(
        max_length=MAX_VIEW_ITEMS, description="The decisions taken, one per item."
    )
    open_threads: tuple[Annotated[str, Field(max_length=MAX_VIEW_ITEM_CHARS)], ...] = Field(
        max_length=MAX_VIEW_ITEMS, description="What is still open, one per item."
    )

    @model_validator(mode="after")
    def _within_total_bound(self) -> CompactView:
        """Reject a view whose sections together exceed MAX_VIEW_CHARS."""
        # Each section is bounded on its own, but all four at their maxima would be twice the
        # whole-view cap; the total is what the requester's max_chars is measured against.
        total = (
            len(self.goal)
            + len(self.progress)
            + sum(len(item) for item in self.decisions)
            + sum(len(item) for item in self.open_threads)
        )
        if total > MAX_VIEW_CHARS:
            raise ValueError(
                f"CompactView totals {total} characters, more than the {MAX_VIEW_CHARS} a view "
                "may hold."
            )
        return self
