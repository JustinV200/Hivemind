"""Write and read Handoffs: the checkpoint half of codingrules 8.9's "one mechanism, many names".

`write_checkpoint` is what a threshold reset, a rebind to another model, a Warden migration or a
takeover all call underneath (codingrules section 8.9): it stores the given Handoff keyed by the
very trail event it records (`memory.checkpoint`), so the event's own id becomes the
`HandoffRef.event_id` a caller keeps to resume from it later. The event's payload carries only ids
and counts (`decision_count`, `pinned_fact_count`, and `task_id` when there is one), never the
Handoff's own text (codingrules section 12: no event ever carries prompt or completion text).
`read_handoff` is the other half: it fetches the stored Handoff and its clearance, and refuses
(`ClearanceError`) to hand it back if that clearance is above the reader's own allowance --
codingrules section 8.9's "a Night Veil bee... never resumes from a Royal Handoff."

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `write_checkpoint` is called by a
    Worker's or Warden's runtime at a threshold reset or on an intervention; `read_handoff` is
    called by whatever resumes a bee from one. Calls into hivemind.cell (HoneyClearance),
    hivemind.memory.context (MemoryContext), hivemind.memory.errors (ClearanceError),
    hivemind.memory.handoff (Handoff), hivemind.memory.store.protocol (MemoryStore),
    hivemind.pheromone (MemoryEvent) and waggle only.

Key invariants:
    - The `memory.checkpoint` event's `subject_id` is `task_id` when one is given, otherwise the
      event's own id: a checkpoint always names something, even a Handoff that concerns no task.
    - `read_handoff` trusts only the store's own returned clearance for its allowance check, never
      a caller-supplied `HandoffRef.clearance` -- the latter is informational for the caller, the
      former is the authoritative check.

See Also:
    - .claude/codingrules.md section 8.9 for "one mechanism, many names" and the Handoff/clearance
      rules this module implements.
    - .claude/codingrules.md section 12 for the payload-carries-no-text rule the checkpoint
      event's payload follows.
    - hivemind.memory.handoff for Handoff, the document this module writes and reads.
    - hivemind.memory.store.protocol for MemoryStore.put_handoff/get_handoff, this module's calls.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.cell import HoneyClearance
from hivemind.memory.context import MemoryContext
from hivemind.memory.errors import ClearanceError
from hivemind.memory.handoff import Handoff
from hivemind.memory.store.protocol import MemoryStore
from hivemind.pheromone import MemoryEvent
from waggle.ids import TaskId, new_event_id
from waggle.messages import HandoffRef

__all__ = ["read_handoff", "write_checkpoint"]


async def write_checkpoint(
    handoff: Handoff, task_id: TaskId | None, ctx: MemoryContext
) -> HandoffRef:
    """Store `handoff` and record a `memory.checkpoint` trail event, atomically.

    Args:
        handoff: The Handoff to store.
        task_id: The task this Handoff concerns, or None when it concerns no one task.
        ctx: The store, identity and clock to write with.

    Returns:
        A HandoffRef whose `event_id` is the trail event's own id: the lookup key `read_handoff`
        (or a later resume) fetches the Handoff back by.
    """
    now = ctx.clock.now()
    event_id = new_event_id(ctx.clock)
    # Counts only, never the Handoff's own text (codingrules section 12).
    payload: dict[str, JsonValue] = {
        "decision_count": len(handoff.decisions),
        "pinned_fact_count": len(handoff.pinned_facts),
    }
    if task_id is not None:
        payload["task_id"] = task_id
    event = MemoryEvent(
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
    await ctx.store.put_handoff(event_id, handoff, task_id, event)
    return HandoffRef(event_id=event_id, written_at=now, clearance=handoff.clearance.to_wire())


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
    """
    handoff, clearance = await store.get_handoff(ref.event_id)
    if clearance.rank > allowance.rank:
        raise ClearanceError(clearance, allowance)
    return handoff
