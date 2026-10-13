"""Define HandoffDocument: a Handoff as it travels inside a task.assign when the receiver lacks it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Handoff is
the structured document a bee (a Worker, the sub-bee that does the work, or a Warden, the
always-on supervisor of one Cell, a unit of compute) writes just before its context is reset: its
goal restated, what it has done, the decisions it made and why, what failed, what constrains it,
what is still open, what to do next, what never to redo and the facts worth keeping verbatim. A
task normally travels with only a ``HandoffRef`` (the trail event that recorded it) because the
receiving Warden reads the document from the memory store it shares with the sender. When it does
not share one (a Warden migrating onto a colonized device, where the Hive Stand's store is out of
reach, or a task reassigned across nodes), the Queen (the central orchestrator) carries the
document itself in ``TaskAssign.handoff`` (PROTOCOL_MINOR 13, roadmap phase 11, ADR-0052), and the
receiver stores it under the reference's event id before it spawns the bee. ``HandoffDocument``
mirrors ``hivemind.memory.Handoff`` field for field with the same bounds, minus the taint label:
a tainted Handoff is one nobody may resume from, so it never travels. ``HandoffDecision`` mirrors
its ``Decision``. A test in hivemind keeps the two in step. Every bound is a named constant here;
the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Carried by waggle.messages.task.assignment
    (TaskAssign.handoff); built by the Queen from a stored Handoff and read by the Warden that
    stores it; calls into waggle.messages.base and waggle.messages.labels only.

Key invariants:
    - Every model here is frozen and forbids extras through VALUE_MODEL_CONFIG, exactly like a
      message, but none subclasses WaggleMessage, so none can ever be registered as a kind.
    - Every field has the bounds of the hivemind Handoff it mirrors: free text is capped, so a
      Handoff briefs the next episode compactly and never carries a transcript.
    - Nothing here carries a taint label; the document that travels is, by construction, one
      that was clean when it was sent.

See Also:
    - docs/waggle/spec.md section 8.2 for the normative fields and bounds.
    - hivemind.memory.handoff for Handoff and Decision, the models these mirror.
    - waggle.messages.task.assignment for TaskAssign.handoff and the rules that govern it.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for why a Warden migration needs it.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from waggle.messages.base import VALUE_MODEL_CONFIG, TaskIdField
from waggle.messages.labels import HoneyClearance

MIN_HANDOFF_GOAL_CHARS = 1  # A Handoff always restates what it is working toward.
MAX_HANDOFF_GOAL_CHARS = 2_000  # A restated goal, not the whole plan; the plan lives on the task.
MIN_HANDOFF_PROGRESS_CHARS = 1  # Always says something, even "nothing done yet".
MAX_HANDOFF_PROGRESS_CHARS = 4_000  # What has been done so far; can run longer than the goal.
MAX_HANDOFF_DECISION_WHAT_CHARS = 500  # One decision, in a sentence or two.
MAX_HANDOFF_DECISION_WHY_CHARS = 500  # Its reason, likewise.
MAX_HANDOFF_DECISIONS = 64  # More decisions than this is really several Handoffs stapled together.
MAX_HANDOFF_LIST_ITEM_CHARS = 500  # One line in tried_and_failed, constraints, open_threads, ...
MAX_HANDOFF_LIST_ITEMS = 64  # Cap on each of those lists' own length.
MAX_HANDOFF_NOTES_CHARS = 2_000  # Freeform notes, capped (roadmap step 3.14).
MAX_HANDOFF_WRITTEN_BY_CHARS = 128  # A bee id or a role name, never free text.

__all__ = [
    "MAX_HANDOFF_DECISIONS",
    "MAX_HANDOFF_DECISION_WHAT_CHARS",
    "MAX_HANDOFF_DECISION_WHY_CHARS",
    "MAX_HANDOFF_GOAL_CHARS",
    "MAX_HANDOFF_LIST_ITEMS",
    "MAX_HANDOFF_LIST_ITEM_CHARS",
    "MAX_HANDOFF_NOTES_CHARS",
    "MAX_HANDOFF_PROGRESS_CHARS",
    "MAX_HANDOFF_WRITTEN_BY_CHARS",
    "MIN_HANDOFF_GOAL_CHARS",
    "MIN_HANDOFF_PROGRESS_CHARS",
    "HandoffDecision",
    "HandoffDocument",
]

# One line in a Handoff's list fields: a short, capped string, named once so every list field
# below shares the exact same element type.
_ListItem = Annotated[str, Field(max_length=MAX_HANDOFF_LIST_ITEM_CHARS)]


class HandoffDecision(BaseModel):
    """One decision a bee made before checkpointing, with its reason."""

    model_config = VALUE_MODEL_CONFIG

    what: str = Field(
        min_length=1, max_length=MAX_HANDOFF_DECISION_WHAT_CHARS, description="What was decided."
    )
    why: str = Field(
        min_length=1, max_length=MAX_HANDOFF_DECISION_WHY_CHARS, description="Why it was decided."
    )


class HandoffDocument(BaseModel):
    """A resumable snapshot of a bee's work, as it crosses Waggle inside a task.assign.

    The wire form of hivemind's Handoff: every field but ``task_id`` is mandatory, so a bee
    resuming from it never has to guess what an absent field meant, and there is no taint label
    because a tainted Handoff is never sent.
    """

    model_config = VALUE_MODEL_CONFIG

    goal: str = Field(
        min_length=MIN_HANDOFF_GOAL_CHARS,
        max_length=MAX_HANDOFF_GOAL_CHARS,
        description="What this is working toward.",
    )
    progress: str = Field(
        min_length=MIN_HANDOFF_PROGRESS_CHARS,
        max_length=MAX_HANDOFF_PROGRESS_CHARS,
        description="What has been done so far.",
    )
    decisions: tuple[HandoffDecision, ...] = Field(
        max_length=MAX_HANDOFF_DECISIONS,
        description="Decisions made so far, each with its reason.",
    )
    tried_and_failed: tuple[_ListItem, ...] = Field(
        max_length=MAX_HANDOFF_LIST_ITEMS,
        description="Approaches already tried that did not work.",
    )
    constraints: tuple[_ListItem, ...] = Field(
        max_length=MAX_HANDOFF_LIST_ITEMS, description="Constraints discovered along the way."
    )
    open_threads: tuple[_ListItem, ...] = Field(
        max_length=MAX_HANDOFF_LIST_ITEMS, description="Loose ends not yet resolved."
    )
    next_steps: tuple[_ListItem, ...] = Field(
        max_length=MAX_HANDOFF_LIST_ITEMS, description="What the resuming bee should do next."
    )
    do_not_redo: tuple[_ListItem, ...] = Field(
        max_length=MAX_HANDOFF_LIST_ITEMS,
        description="Things already tried that must not be repeated.",
    )
    pinned_facts: tuple[_ListItem, ...] = Field(
        max_length=MAX_HANDOFF_LIST_ITEMS,
        description="Facts copied verbatim from a pin, never summarised.",
    )
    notes: str = Field(max_length=MAX_HANDOFF_NOTES_CHARS, description="Freeform notes, capped.")
    clearance: HoneyClearance = Field(description="This Handoff's data-sensitivity label.")
    written_by: str = Field(
        max_length=MAX_HANDOFF_WRITTEN_BY_CHARS,
        description="The bee id or role that wrote this Handoff.",
    )
    task_id: TaskIdField | None = Field(
        default=None,
        description="The task this Handoff concerns, if it concerns one at all.",
    )
