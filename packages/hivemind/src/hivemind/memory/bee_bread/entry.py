"""Define BeeBreadEntryKind and BeeBreadEntry: the warm tier's one row shape.

Bee Bread (the warm memory tier, named for the bees' own fermented pollen store: not what is
eaten first, but what keeps until it is needed) sits between hot state and Honey (the cold tier,
phase 7): "recent episodes, task results, recent Nectar, Handoffs... lookup by id, time, task; no
search" (codingrules section 8.9). Every entry is one `BeeBreadEntry`: what kind of thing it
indexes (`BeeBreadEntryKind`), the ids of what it references in Brood Chamber (the task store) or
the Pheromone Trail (`ref_ids` -- `hivemind.memory` may not import `hivemind.brood_chamber`, so an
entry never holds the referenced record itself, only its id), which task it concerns, its
`HoneyClearance`, when it was written, an optional short preview capped by the manifest's
`item_cap_chars` (`text`), and an optional full payload for the kinds whose content lives here
and nowhere else yet (`payload`, for `TRANSCRIPT`, `TOOL_RESULT` and `SUMMARY`: the Honey Store does
not exist until phase 7, so a deposited transcript, an oversized tool result, or a compacted summary
(roadmap step 4.3, `hivemind.memory.compact.compact`) is stored here in full, and whatever holds the
entry's own id -- hot state, a Handoff -- carries only that id as its "reference", never the payload
text itself). `SUMMARY` is what `compact` deposits: never itself a source for a further compaction
(docs/adr/0022: "never from a previous summary... one level of summary"), so `compact` refuses an
entry of this kind offered to it as a source.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read and written by
    hivemind.memory.bee_bread.index (BeeBread) and hivemind.memory.bee_bread.deposit (the write
    paths); persisted by hivemind.memory.store (the memory_bee_bread table). Calls into
    hivemind.cell (HoneyClearance) and waggle only.

Key invariants:
    - Every BeeBreadEntry carries a HoneyClearance (codingrules section 8.9: "every tier carries a
      clearance"); every lookup in hivemind.memory.bee_bread.index filters by the reader's
      allowance before returning one.
    - `text` and `payload` are never both set by this dispatch's own writers
      (hivemind.memory.bee_bread.deposit, hivemind.memory.compact): an index-only entry
      (TASK_HISTORY, TRAIL_EVENT, HANDOFF, NOTE) carries `text` and no `payload`; a full-content
      entry (TRANSCRIPT, TOOL_RESULT, SUMMARY) carries `payload` and no `text`. Nothing in this
      model enforces that split; it is a convention its writer modules follow.

See Also:
    - .claude/codingrules.md section 8.9 for the Bee Bread tier this model implements.
    - .claude/roadmap.md step 4.2 for BeeBreadEntry's field list verbatim.
    - hivemind.memory.bee_bread.index for BeeBread, the lookup-only reader of this model.
    - hivemind.memory.bee_bread.deposit for every write path that constructs one.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from waggle.messages.base import EventIdField, TaskIdField, UtcDatetime

MAX_REF_IDS = 16  # Most entries index a handful of records; a SUMMARY entry (hivemind.memory.
# compact) references every source it folds in, so the cap is generous enough for one compaction
# batch while a House Bee sweep still chunks a larger backlog into several batches, each within
# this same cap (roadmap step 4.3).
MAX_ENTRY_TEXT_CHARS = 2_000  # A short preview line, roughly matching a hot-state item's own cap.
# A generous ceiling for a full transcript or an oversized tool result, not the [memory]
# item_cap_chars cap (that governs hot-state preview text only, in hivemind.memory.hot_state.
# packing). Large enough for a real working session's transcript; still bounded so one deposit can
# never grow the table without limit.
MAX_ENTRY_PAYLOAD_CHARS = 200_000

__all__ = [
    "MAX_ENTRY_PAYLOAD_CHARS",
    "MAX_ENTRY_TEXT_CHARS",
    "MAX_REF_IDS",
    "BeeBreadEntry",
    "BeeBreadEntryKind",
]


class BeeBreadEntryKind(Enum):
    """What a BeeBreadEntry indexes or holds."""

    TASK_HISTORY = "TASK_HISTORY"  # References a Brood Chamber task by id.
    TRAIL_EVENT = "TRAIL_EVENT"  # References a Pheromone Trail event by id (an Alarm, a question).
    HANDOFF = "HANDOFF"  # References a stored Handoff by its memory.checkpoint event id.
    NOTE = "NOTE"  # References (and previews) a demoted Note's own text.
    TRANSCRIPT = "TRANSCRIPT"  # Holds a deposited transcript's own text, in full, as `payload`.
    TOOL_RESULT = "TOOL_RESULT"  # Holds an oversized tool result's own text, in full, as `payload`.
    SUMMARY = "SUMMARY"  # Holds a compacted summary of other entries, in full, as `payload`; never
    # itself a source for a further compaction (hivemind.memory.compact: one level only).
    RECORDING = "RECORDING"  # References an Exoskeleton flight recording by id (roadmap 6.6); its
    # frames stay in the RecordingStore, the recording's Nectar body until phase 7 (ADR-0032).


class BeeBreadEntry(BaseModel):
    """One warm-tier row: an index into Brood Chamber/the trail, or a deposited payload in full.

    See the module docstring for the `text`-vs-`payload` convention this dispatch's writers follow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventIdField = Field(
        description="This entry's own id: an EventId-shaped ULID; no BeeBreadEntryId kind exists."
    )
    kind: BeeBreadEntryKind = Field(description="What this entry indexes or holds.")
    ref_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REF_IDS,
        description="Ids of the Brood Chamber or trail records this entry indexes, if any.",
    )
    task_id: TaskIdField | None = Field(
        default=None, description="The task this entry concerns, if it concerns one at all."
    )
    clearance: HoneyClearance = Field(description="This entry's data-sensitivity label.")
    created_at: UtcDatetime = Field(description="When this entry was written.")
    text: str | None = Field(
        default=None,
        max_length=MAX_ENTRY_TEXT_CHARS,
        description="A short, capped preview; set for an index-only entry, unset for a payload.",
    )
    payload: str | None = Field(
        default=None,
        max_length=MAX_ENTRY_PAYLOAD_CHARS,
        description="The full content for a TRANSCRIPT, TOOL_RESULT or SUMMARY entry; unset "
        "otherwise.",
    )
