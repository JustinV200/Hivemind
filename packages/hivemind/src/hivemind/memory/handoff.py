"""Define Decision and Handoff: the resumable snapshot a bee writes before its context resets.

A Handoff is a structured document a bee (a Worker or a Warden) writes just before it checkpoints:
its goal restated, what it has done, the decisions it made and why, what it tried that failed, the
constraints it discovered, what is still open, what to do next, what never to redo, and the facts
worth pinning verbatim. Codingrules section 8.9: "One mechanism, many names" -- a threshold reset,
a rebind to another model, a Warden migrating to another host, Clustering (pausing while a provider
is unreachable) and Requeening (recovering a crashed Queen) are all the same operation underneath:
checkpoint, write a Handoff, resume from it on a possibly different slot or host. Every free-text
field here is capped (the caps are commented module constants below, never a magic number inline),
because a Handoff is meant to brief the next episode compactly, not to carry a transcript.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Written by a Worker or a Warden through
    `hivemind.memory.checkpoint.write_checkpoint`; read back through `hivemind.memory.checkpoint.
    read_handoff` to resume the same work. Calls into hivemind.cell (for HoneyClearance) and
    waggle only.

Key invariants:
    - Every free-text field is capped (codingrules section 8.9: "Freeform text is capped").
    - pinned_facts are meant to be copied verbatim from `hivemind.memory.pins.Pin.text`, never
      summarised (codingrules section 8.9: compaction "copies pins verbatim").
    - Every field except `task_id` is mandatory (codingrules section 8.9's "mandatory fields"):
      a caller must pass an explicit value, even an empty tuple, for each one.
    - clearance is the Handoff's own label; `hivemind.memory.checkpoint.read_handoff` refuses to
      return one above a reader's allowance (a ClearanceError).

See Also:
    - .claude/codingrules.md section 8.9 for the Handoff shape and "one mechanism, many names".
    - hivemind.memory.checkpoint for write_checkpoint/read_handoff, the write and read paths.
    - hivemind.cell for HoneyClearance.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from waggle.messages.base import TaskIdField

MIN_GOAL_CHARS = 1  # A Handoff always restates what it is working toward.
MAX_GOAL_CHARS = 2_000  # A restated goal, not the whole plan; the plan itself lives on the task.
MIN_PROGRESS_CHARS = 1  # Always says something, even "nothing done yet".
MAX_PROGRESS_CHARS = 4_000  # What has been done so far; can run longer than the goal itself.
MAX_DECISION_WHAT_CHARS = 500  # One decision, in a sentence or two.
MAX_DECISION_WHY_CHARS = 500  # Its reason, likewise.
MAX_DECISIONS = 64  # More decisions than this is really several Handoffs stapled together.
MAX_LIST_ITEM_CHARS = 500  # One line in tried_and_failed/constraints/open_threads/etc.
MAX_LIST_ITEMS = 64  # Cap on each of those lists' own length.
MAX_NOTES_CHARS = 2_000  # roadmap step 3.14: Handoff.notes is "capped notes".
MAX_WRITTEN_BY_CHARS = 128  # An id or a role name, never free text.

__all__ = [
    "MAX_DECISIONS",
    "MAX_DECISION_WHAT_CHARS",
    "MAX_DECISION_WHY_CHARS",
    "MAX_GOAL_CHARS",
    "MAX_LIST_ITEMS",
    "MAX_LIST_ITEM_CHARS",
    "MAX_NOTES_CHARS",
    "MAX_PROGRESS_CHARS",
    "MAX_WRITTEN_BY_CHARS",
    "MIN_GOAL_CHARS",
    "MIN_PROGRESS_CHARS",
    "Decision",
    "Handoff",
]

# One line in a Handoff's list fields (tried_and_failed, constraints, ...): a short, capped string,
# named once so every one of those tuple fields below shares the exact same element type.
_ListItem = Annotated[str, Field(max_length=MAX_LIST_ITEM_CHARS)]


class Decision(BaseModel):
    """One decision a bee made before checkpointing, with its reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    what: str = Field(
        min_length=1, max_length=MAX_DECISION_WHAT_CHARS, description="What was decided."
    )
    why: str = Field(
        min_length=1, max_length=MAX_DECISION_WHY_CHARS, description="Why it was decided."
    )


class Handoff(BaseModel):
    """A structured, resumable snapshot of a bee's work, written before its context resets.

    Every field but `task_id` is mandatory: even a short-lived task's Handoff records something
    for each, so a bee resuming from it (on the same slot or a different one, codingrules section
    8.9's "one mechanism, many names") never has to guess what an absent field meant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str = Field(
        min_length=MIN_GOAL_CHARS,
        max_length=MAX_GOAL_CHARS,
        description="What this is working toward.",
    )
    progress: str = Field(
        min_length=MIN_PROGRESS_CHARS,
        max_length=MAX_PROGRESS_CHARS,
        description="What has been done so far.",
    )
    decisions: tuple[Decision, ...] = Field(
        max_length=MAX_DECISIONS, description="Decisions made so far, each with its reason."
    )
    tried_and_failed: tuple[_ListItem, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Approaches already tried that did not work."
    )
    constraints: tuple[_ListItem, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Constraints discovered along the way."
    )
    open_threads: tuple[_ListItem, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Loose ends not yet resolved."
    )
    next_steps: tuple[_ListItem, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="What the resuming bee should do next."
    )
    do_not_redo: tuple[_ListItem, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Things already tried that must not be repeated."
    )
    pinned_facts: tuple[_ListItem, ...] = Field(
        max_length=MAX_LIST_ITEMS,
        description="Facts copied verbatim from Pin.text, never summarised.",
    )
    notes: str = Field(
        max_length=MAX_NOTES_CHARS, description="Freeform notes, capped (roadmap step 3.14)."
    )
    clearance: HoneyClearance = Field(description="This Handoff's data-sensitivity label.")
    written_by: str = Field(
        max_length=MAX_WRITTEN_BY_CHARS, description="The bee id or role that wrote this Handoff."
    )
    task_id: TaskIdField | None = Field(
        default=None, description="The task this Handoff concerns, if it concerns one at all."
    )
