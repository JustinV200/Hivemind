"""Write and read Handoffs: the checkpoint half of codingrules 8.9's "one mechanism, many names".

`write_checkpoint` is what a threshold reset, a rebind to another model, a Warden migration or a
takeover all call underneath (codingrules section 8.9): it stores the given Handoff keyed by the
very trail event it records (`memory.checkpoint`), so the event's own id becomes the
`HandoffRef.event_id` a caller keeps to resume from it later. The event's payload carries only ids
and counts (`decision_count`, `pinned_fact_count`, and `task_id` when there is one), never the
Handoff's own text (codingrules section 12: no event ever carries prompt or completion text). It
also makes real the other half of "one mechanism, many names" this module's docstring used to only
describe: "deposit the transcript as Nectar" (roadmap step 4.2, since the Honey Store does not
exist until phase 7, "Nectar" here is a Bee Bread TRANSCRIPT entry) -- every checkpoint deposits a
small Bee Bread entry indexing the Handoff itself (`hivemind.memory.bee_bread.deposit.
deposit_handoff_ref`), so "Stored Handoffs... are entries too" holds without a second store-level
search path, and a caller that also has the episode's transcript text may pass it to deposit it in
full. `read_handoff` is the other half: it fetches the stored Handoff and its clearance, and refuses
(`ClearanceError`) to hand it back if that clearance is above the reader's own allowance --
codingrules section 8.9's "a Night Veil bee... never resumes from a Royal Handoff." -- and, since
roadmap step 10.6d, refuses (`TaintedMemoryError`) a Handoff labelled TAINTED, whatever the reader's
clearance: memory written while a bee may have been compromised is never resumed from until a
judge verdict clears it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `write_checkpoint` is called by a
    Worker's or Warden's runtime at a threshold reset or on an intervention; `read_handoff` is
    called by whatever resumes a bee from one. Calls into hivemind.cell (HoneyClearance),
    hivemind.memory.bee_bread (deposit_handoff_ref, deposit_transcript), hivemind.memory.context
    (MemoryContext), hivemind.memory.errors (ClearanceError), hivemind.memory.handoff (Handoff),
    hivemind.memory.store.protocol (MemoryStore), hivemind.pheromone (MemoryEvent) and waggle only.

Key invariants:
    - The `memory.checkpoint` event's `subject_id` is `task_id` when one is given, otherwise the
      event's own id: a checkpoint always names something, even a Handoff that concerns no task.
    - `read_handoff` trusts only the store's own returned clearance for its allowance check, never
      a caller-supplied `HandoffRef.clearance` -- the latter is informational for the caller, the
      former is the authoritative check.
    - `read_handoff` never returns a TAINTED Handoff (roadmap 10.6d); a CLEARED one it returns.
    - `write_checkpoint`'s `transcript` parameter is optional and defaults to `None` (no deposit),
      so `hivemind.workers.runtime.attempt`'s existing three-positional-argument call keeps working
      unchanged (codingrules: keep a public signature stable unless a new parameter has a default).

See Also:
    - .claude/codingrules.md section 8.9 for "one mechanism, many names" and the Handoff/clearance
      rules this module implements.
    - .claude/codingrules.md section 12 for the payload-carries-no-text rule the checkpoint
      event's payload follows.
    - .claude/roadmap.md step 4.2 for the transcript-deposit requirement this module makes real.
    - hivemind.memory.handoff for Handoff, the document this module writes and reads.
    - hivemind.memory.bee_bread for deposit_handoff_ref and deposit_transcript, this module's two
      new write paths.
    - hivemind.memory.store.protocol for MemoryStore.put_handoff/get_handoff, this module's calls.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import JsonValue

from hivemind.cell import HoneyClearance
from hivemind.memory.bee_bread import deposit_handoff_ref, deposit_transcript
from hivemind.memory.context import MemoryContext
from hivemind.memory.errors import ClearanceError, TaintedMemoryError
from hivemind.memory.handoff import Handoff
from hivemind.memory.store.protocol import MemoryStore
from hivemind.pheromone import MemoryEvent
from waggle.ids import TaskId, new_event_id
from waggle.messages import HandoffRef

__all__ = ["read_handoff", "write_checkpoint"]


async def write_checkpoint(
    handoff: Handoff,
    task_id: TaskId | None,
    ctx: MemoryContext,
    transcript: str | None = None,
) -> HandoffRef:
    """Store `handoff`, record a `memory.checkpoint` trail event, and deposit it into Bee Bread.

    Args:
        handoff: The Handoff to store.
        task_id: The task this Handoff concerns, or None when it concerns no one task.
        ctx: The store, identity and clock to write with.
        transcript: The episode's own transcript text, when the caller has one to deposit in full
            (roadmap step 4.2's "deposit the transcript as Nectar"). `None` (the default) deposits
            nothing beyond the Handoff-indexing entry every checkpoint writes regardless.

    Returns:
        A HandoffRef whose `event_id` is the trail event's own id: the lookup key `read_handoff`
        (or a later resume) fetches the Handoff back by.
    """
    now = ctx.clock.now()
    event_id = new_event_id(ctx.clock)
    event = _checkpoint_event(handoff, task_id, event_id, now, ctx)
    await ctx.store.put_handoff(event_id, handoff, task_id, event)

    # Every checkpoint indexes its own Handoff into Bee Bread (roadmap step 4.2: "Stored
    # Handoffs... are entries too"), and deposits the transcript in full when the caller has one.
    await deposit_handoff_ref(event_id, task_id, handoff.clearance, ctx)
    if transcript is not None:
        await deposit_transcript(transcript, task_id, handoff.clearance, ctx)

    return HandoffRef(event_id=event_id, written_at=now, clearance=handoff.clearance.to_wire())


def _checkpoint_event(
    handoff: Handoff, task_id: TaskId | None, event_id: str, now: datetime, ctx: MemoryContext
) -> MemoryEvent:
    """Build the `memory.checkpoint` event `write_checkpoint` records; counts only, never text."""
    payload: dict[str, JsonValue] = {
        "decision_count": len(handoff.decisions),
        "pinned_fact_count": len(handoff.pinned_facts),
    }
    if task_id is not None:
        payload["task_id"] = task_id
    return MemoryEvent(
        id=event_id,
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=now,
        actor=ctx.identity.actor,
        kind="memory.checkpoint",
        # About the task when there is one; otherwise about the Handoff's own event, since a
        # checkpoint need not concern any task at all (a Queen-level Handoff, say).
        subject_id=task_id if task_id is not None else event_id,
        payload=payload,
    )


async def read_handoff(store: MemoryStore, ref: HandoffRef, allowance: HoneyClearance) -> Handoff:
    """Fetch the Handoff `ref` names, refusing one above `allowance`.

    Args:
        store: Where the Handoff is stored.
        ref: The reference (from a prior `write_checkpoint`) naming which Handoff to fetch.
        allowance: The reader's clearance ceiling.

    Returns:
        The stored Handoff.

    Raises:
        hivemind.memory.errors.HandoffNotFoundError: No Handoff with `ref.event_id` exists.
        ClearanceError: The stored Handoff's clearance is above `allowance`.
        TaintedMemoryError: The stored Handoff is labelled TAINTED (roadmap 10.6d).
    """
    handoff, clearance = await store.get_handoff(ref.event_id)
    if clearance.rank > allowance.rank:
        raise ClearanceError(clearance, allowance)
    # Refused outright, whatever the clearance allows: only a judge verdict makes it usable again.
    if handoff.tainted is not None and handoff.tainted.refuses:
        raise TaintedMemoryError(ref.event_id, handoff.tainted.event_id)
    return handoff
