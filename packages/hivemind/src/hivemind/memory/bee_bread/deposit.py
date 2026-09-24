"""Define every write path into Bee Bread: deposits and the demotion archive.

Four shapes of write, one underlying mechanism (`_deposit`, which stores the entry and records its
`memory.bee_bread_deposited` trail event atomically, codingrules section 12): `deposit_transcript`
and `deposit_tool_result` hold a full payload in the entry itself, because the Honey Store does not
exist until phase 7 (roadmap step 4.1: "large tool results become Nectar with a reference" --
Nectar's actual home; until then, Bee Bread holds it and the reference is this entry's own id).
`deposit_transcript` is what `hivemind.memory.checkpoint.write_checkpoint` calls to make good on
codingrules section 8.9's "one mechanism, many names": "checkpoint, write a Handoff, deposit the
transcript as Nectar, resume..." -- `deposit_tool_result` is exposed here for a Worker's tool loop
to call later (not wired into `hivemind.workers` by this dispatch: workers/ belongs to another
implementer). `deposit_handoff_ref` is the other half of "Stored Handoffs... are entries too"
(roadmap step 4.2): it indexes an already-stored Handoff by the `memory.checkpoint` event id that
names it, so `hivemind.memory.bee_bread.index.BeeBread.by_task`/`.between` surface Handoffs without
a second store-level search path. `deposit_hot_state_item` is what
`hivemind.memory.demote.demote` calls to archive a hot-state candidate that is leaving hot state.

`deposit_dropped_items` (roadmap step 4.4) is the flood test's own bridge: `hivemind.memory.
hot_state.packing.assemble`'s optional `on_drop` callback lets a caller collect every candidate the
packer left out of a budgeted prompt (`Prompt.dropped` already carries their ids; `on_drop` hands
the caller the items themselves), and this function archives each one the same way `demote` does,
so "every dropped item is findable in Bee Bread by id" (roadmap step 4.4) holds for hot state that
never even made it into one episode's prompt, not only for items a House Bee sweep later demotes.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `deposit_transcript` and
    `deposit_handoff_ref` are called by hivemind.memory.checkpoint.write_checkpoint;
    `deposit_hot_state_item` is called by hivemind.memory.demote.demote; `deposit_dropped_items` is
    called by hivemind.queen.awake.episode.decide_awake and hivemind.wardens.awake.episode.
    decide_awake, after hivemind.memory.hot_state.packing.assemble; `deposit_tool_result` is
    exposed for a future Worker tool-loop caller. Calls into hivemind.cell (HoneyClearance),
    hivemind.common.errors (InvariantViolationError), hivemind.memory.bee_bread.entry,
    hivemind.memory.context (MemoryContext), hivemind.memory.relevance (Scorable), hivemind.
    memory.hot_state.summaries, hivemind.memory.notes, hivemind.memory.pins, hivemind.pheromone
    (MemoryEvent) and waggle only.

Key invariants:
    - Every public function here ends by calling `_deposit`, so every entry this module writes
      commits with its trail event in the same store call (codingrules section 12).
    - `deposit_hot_state_item` is never called with a Pin (`hivemind.memory.demote.should_demote`
      never returns a reason for one; pins never decay). Called with one anyway, it raises
      `InvariantViolationError` rather than silently archiving something that should never leave
      hot state.
    - `deposit_dropped_items` skips a Pin or a CellWaxSummary rather than raising: unlike
      `deposit_hot_state_item`'s own callers (which never pass one, by construction), packing's
      `on_drop` callback can in principle collect either under an extreme budget, and both have
      their own lifecycle that this function must not interrupt (see `_archive_shape`'s own two
      refusals for why).

See Also:
    - .claude/codingrules.md section 8.9 for "one mechanism, many names" and the transcript-deposit
      rule this module makes real for checkpoint.py.
    - .claude/roadmap.md step 4.2 for the entries-cover-Handoffs-and-transcripts requirement.
    - hivemind.memory.bee_bread.entry for BeeBreadEntry and BeeBreadEntryKind.
    - hivemind.memory.checkpoint for write_checkpoint, the caller that makes deposit_transcript
      and deposit_handoff_ref real.
    - hivemind.memory.demote for demote, the caller of deposit_hot_state_item.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from hivemind.cell import HoneyClearance
from hivemind.common.errors import InvariantViolationError
from hivemind.memory.bee_bread.entry import BeeBreadEntry, BeeBreadEntryKind
from hivemind.memory.context import MemoryContext
from hivemind.memory.hot_state.summaries import (
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.notes import Note
from hivemind.memory.pins import Pin
from hivemind.memory.relevance import Scorable
from hivemind.pheromone import MemoryEvent
from waggle.ids import EventId, TaskId, new_event_id

__all__ = [
    "deposit_dropped_items",
    "deposit_handoff_ref",
    "deposit_hot_state_item",
    "deposit_tool_result",
    "deposit_transcript",
]


async def deposit_transcript(
    transcript: str, task_id: TaskId | None, clearance: HoneyClearance, ctx: MemoryContext
) -> BeeBreadEntry:
    """Deposit `transcript` in full as a TRANSCRIPT entry.

    Args:
        transcript: The transcript text, capped by `BeeBreadEntry.payload`'s own field limit.
        task_id: The task this transcript concerns, if any.
        clearance: The deposited entry's own label (the checkpointing principal's clearance).
        ctx: The store, identity and clock to write with.

    Returns:
        The stored BeeBreadEntry; its `id` is the reference a caller keeps.
    """
    entry = _build_payload_entry(BeeBreadEntryKind.TRANSCRIPT, transcript, task_id, clearance, ctx)
    await _deposit(entry, ctx)
    return entry


async def deposit_tool_result(
    text: str, task_id: TaskId | None, clearance: HoneyClearance, ctx: MemoryContext
) -> BeeBreadEntry:
    """Deposit an oversized tool result in full as a TOOL_RESULT entry.

    Exposed for a Worker's tool loop to call once a tool result is too large for hot state; not
    wired into any caller by this dispatch (workers/ is owned by another implementer).

    Args:
        text: The tool result text, capped by `BeeBreadEntry.payload`'s own field limit.
        task_id: The task this result concerns, if any.
        clearance: The deposited entry's own label.
        ctx: The store, identity and clock to write with.

    Returns:
        The stored BeeBreadEntry; its `id` is the reference a caller keeps in hot state instead of
        the raw text.
    """
    entry = _build_payload_entry(BeeBreadEntryKind.TOOL_RESULT, text, task_id, clearance, ctx)
    await _deposit(entry, ctx)
    return entry


async def deposit_handoff_ref(
    event_id: EventId, task_id: TaskId | None, clearance: HoneyClearance, ctx: MemoryContext
) -> BeeBreadEntry:
    """Index an already-stored Handoff so Bee Bread's own lookups surface it.

    Args:
        event_id: The `memory.checkpoint` trail event id the Handoff is stored under
            (`hivemind.memory.checkpoint.write_checkpoint`'s own `HandoffRef.event_id`).
        task_id: The task the Handoff concerns, if any.
        clearance: The Handoff's own clearance.
        ctx: The store, identity and clock to write with.

    Returns:
        The stored BeeBreadEntry, referencing the Handoff by `event_id` rather than holding it.
    """
    entry = BeeBreadEntry(
        id=new_event_id(ctx.clock),
        kind=BeeBreadEntryKind.HANDOFF,
        ref_ids=(event_id,),
        task_id=task_id,
        clearance=clearance,
        created_at=ctx.clock.now(),
    )
    await _deposit(entry, ctx)
    return entry


async def deposit_recording_ref(
    recording_id: str, task_id: TaskId | None, clearance: HoneyClearance, ctx: MemoryContext
) -> BeeBreadEntry:
    """Index an Exoskeleton flight recording so an episode record can reach it (roadmap 6.6).

    Args:
        recording_id: The recording's id in the RecordingStore that holds its frames.
        task_id: The task it was recorded for, if any.
        clearance: What the recorded screens may show: the task's own clearance.
        ctx: The store, identity and clock to write with.

    Returns:
        The stored BeeBreadEntry, referencing the recording rather than holding any frame.
    """
    entry = BeeBreadEntry(
        id=new_event_id(ctx.clock),
        kind=BeeBreadEntryKind.RECORDING,
        ref_ids=(recording_id,),
        task_id=task_id,
        clearance=clearance,
        created_at=ctx.clock.now(),
    )
    await _deposit(entry, ctx)
    return entry


async def deposit_hot_state_item(item: Scorable, ctx: MemoryContext) -> BeeBreadEntry:
    """Archive a hot-state candidate that `hivemind.memory.demote` is moving out of hot state.

    Args:
        item: The candidate leaving hot state; never a Pin (see the module docstring).
        ctx: The store, identity and clock to write with.

    Returns:
        The stored BeeBreadEntry indexing `item`.

    Raises:
        hivemind.common.errors.InvariantViolationError: `item` is a Pin.
    """
    kind, ref_ids, task_id, text = _archive_shape(item)
    entry = BeeBreadEntry(
        id=new_event_id(ctx.clock),
        kind=kind,
        ref_ids=ref_ids,
        task_id=task_id,
        clearance=item.clearance,
        created_at=ctx.clock.now(),
        text=text,
    )
    await _deposit(entry, ctx)
    return entry


async def deposit_dropped_items(
    dropped: Sequence[Scorable], ctx: MemoryContext
) -> tuple[BeeBreadEntry, ...]:
    """Archive every dropped hot-state candidate `assemble`'s own `on_drop` collected.

    Roadmap step 4.4: "every dropped item is findable in Bee Bread by id." A Pin or a
    CellWaxSummary in `dropped` is skipped, never archived (module docstring): both have their own
    lifecycle, and `deposit_hot_state_item` would raise `InvariantViolationError` for either.

    Args:
        dropped: Items `hivemind.memory.hot_state.packing.assemble`'s `on_drop` callback collected
            for one episode's prompt.
        ctx: The store, identity and clock to write with.

    Returns:
        One `BeeBreadEntry` per archivable item, in `dropped`'s own order.
    """
    entries: list[BeeBreadEntry] = []
    for item in dropped:
        if isinstance(item, (Pin, CellWaxSummary)):
            continue  # Neither ever demotes; see this function's own docstring.
        entries.append(await deposit_hot_state_item(item, ctx))
    return tuple(entries)


def _build_payload_entry(
    kind: BeeBreadEntryKind,
    payload: str,
    task_id: TaskId | None,
    clearance: HoneyClearance,
    ctx: MemoryContext,
) -> BeeBreadEntry:
    """Build a full-payload entry (TRANSCRIPT or TOOL_RESULT); shared by the two deposit_* above."""
    return BeeBreadEntry(
        id=new_event_id(ctx.clock),
        kind=kind,
        task_id=task_id,
        clearance=clearance,
        created_at=ctx.clock.now(),
        payload=payload,
    )


def _archive_shape(
    item: Scorable,
) -> tuple[BeeBreadEntryKind, tuple[str, ...], TaskId | None, str | None]:
    """Return the (kind, ref_ids, task_id, preview text) `deposit_hot_state_item` builds from."""
    match item:
        case TaskSummary():
            return BeeBreadEntryKind.TASK_HISTORY, (item.id,), item.id, item.title
        case AlarmSummary():
            return BeeBreadEntryKind.TRAIL_EVENT, (item.id,), item.task_id, item.detail
        case QuestionSummary():
            return BeeBreadEntryKind.TRAIL_EVENT, (item.id,), item.task_id, item.text
        case DecisionSummary():
            return BeeBreadEntryKind.TRAIL_EVENT, (item.episode_id,), None, item.decision
        case Note():
            return BeeBreadEntryKind.NOTE, (item.id,), None, item.text
        case Pin():
            # Pins never demote (hivemind.memory.demote.should_demote never returns a reason for
            # one); a caller reaching here regardless has broken that invariant.
            raise InvariantViolationError("bee_bread cannot archive a Pin: pins never demote")
        case CellWaxSummary():
            # Cell Wax has its own lifecycle (hivemind.memory.cell_wax.writes: WRITTEN -> CLEARED
            # or EXPIRED, driven by a Queen decision or the House Bee sweep's own expiry hook),
            # never the generic hot-state demotion path; should_demote never returns a reason for
            # one (roadmap step 4.2a), matching Pin's own invariant above.
            raise InvariantViolationError("bee_bread cannot archive Cell Wax: it has its own life")
        case _ as unreachable:
            assert_never(unreachable)


async def _deposit(entry: BeeBreadEntry, ctx: MemoryContext) -> None:
    """Store `entry` and record its `memory.bee_bread_deposited` trail event, atomically."""
    event = MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind="memory.bee_bread_deposited",
        subject_id=entry.id,
        payload={"entry_kind": entry.kind.value},
    )
    await ctx.store.add_bee_bread_entry(entry, event)
