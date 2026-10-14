"""Recall a remote Warden's newest Handoff from the Honey Store, so a task can resume from it.

A colonized device's Warden keeps its memory in a store of its own (ADR-0052), and so does a Virtual
Cell's, so the Handoffs their bees write are not in the Queen's store, and a device that is lost
takes them out of her reach altogether. What she can hold is what was sent up. Each checkpoint's
bee also deposits the Handoff as HANDOFF Nectar, which its Warden relays and her intake keeps in the
Honey Store (ADR-0035): best effort, because the Handoff is already durable where it was written.
Nothing else carries the document. The `task.progress` at CHECKPOINTED names a Handoff and its
Warden does not forward it; the trail segment a Warden syncs carries a `memory.checkpoint` event's
ids and counts, never its text (codingrules 12). So `recall_handoff` reads that deposit: it picks
the task's newest Handoff the Queen does not hold already, checks that it may be used, and files it
in her own memory as a checkpoint of her own, which is where the task's next placement resumes from,
and from where she carries it inline to a Warden on another node (`hivemind.queen.dispatcher`).
Every path that resumes a task from a Handoff a remote Warden's bee wrote asks here, so that all of
them refuse the same things: a device taken back, a Warden restarted under new terms, and a
provider coming back from Clustering. A Level 0 device's Handoffs are in her store already, so for
it nothing is newer and nothing is recalled.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's swarm
    gate. Called by `hivemind.queen.swarm_gate.takeback` for every task it takes back, by
    `hivemind.queen.swarm_gate.colonize.steps.pause_for_move` for every task a Warden's move or
    renewal pauses, and by `hivemind.queen.cluster.protocol.resume` for every task Clustering
    resumes. Calls into `hivemind.honey_store` (the store, `held_by_floor_alone`, `raise_label`),
    `hivemind.memory` (Handoff, `write_checkpoint`), `hivemind.pheromone`, `hivemind.queen.cluster`
    (the Handoff she holds), `hivemind.queen.isolation` (`read_isolation`), `hivemind.queen.trail`
    and waggle only; `QueenDeps` for the type.

Key invariants:
    - A Handoff is recalled only if it is newer than the one the Queen holds, and only into her
      own memory, as a checkpoint she writes (a new event id): the device's own event stays the
      device's. Newer is by the checkpoint's own clock, the device's; one whose clock runs behind
      hers by more than the time between two checkpoints is read as older, which costs work only.
    - A Night Veil Cell's memory is never recalled (ADR-0030): the store does not offer its rows.
    - Nothing tainted is recalled: not a Handoff her trail says is tainted and not since cleared,
      and not one a Cell that is isolated now wrote since its first suspect instant, whose
      Warden may never have been given the order to label it.
    - Nothing above the task's clearance is recalled. A Handoff is filed at its own label, raised
      to its Nectar's unless the Nectar's is only the Real Cell floor (a device is a borrowed
      Cell, so everything it deposits is C2 in Honey, which guards retrieval and not the
      task's own state): the same trust a Level 0 device's Handoff already gets.
    - A refusal is never an error: the task resumes from the Handoff she holds or starts fresh,
      and the trail says why. Events carry ids and codes only, never the Handoff's text.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for when a task is taken back,
      and its amendments on recalling a Handoff.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the HANDOFF Nectar and its label.
    - hivemind.workers.runtime.honey for the deposit this reads.
    - hivemind.queen.dispatcher.ready.handoff for how the filed Handoff reaches its new Warden.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from hivemind.brood_chamber import Task
from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.honey_store.clearance import held_by_floor_alone, raise_label
from hivemind.honey_store.errors import NectarNotFoundError
from hivemind.honey_store.models import Nectar
from hivemind.honey_store.store import HoneyStore
from hivemind.memory import Handoff, MemoryContext, write_checkpoint
from hivemind.memory.taint import TAINT_CLEARED_KIND, TAINTED_KIND
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.cluster import latest_handoff
from hivemind.queen.deps import QueenDeps
from hivemind.queen.isolation import IsolationState, read_isolation
from hivemind.queen.trail import record_event
from waggle.ids import CellId, EventId
from waggle.messages import HandoffRef

# How many of a task's Handoffs are looked through, newest first, for one that may be used. A task
# checkpoints at a threshold reset or an intervention, so a few is every one that matters; if the
# newest are all refused, the older ones are not trusted over the one she already holds.
MAX_CANDIDATES = 8

# Why a Handoff was not recalled, as the trail names it (`queen.decided` detail).
# Her trail says it is tainted, and no judge has cleared it since.
NOT_RECALLED_TAINTED = "tainted"
# Written since the first suspect instant of an isolation that stands.
NOT_RECALLED_ISOLATED = "isolated"
# More sensitive than the task may read.
NOT_RECALLED_ABOVE_CLEARANCE = "above_clearance"
# Not a Handoff she can read: its bytes, its shape, its label, or the event that names it.
NOT_RECALLED_UNREADABLE = "unreadable"

log = get_logger(__name__)

__all__ = [
    "MAX_CANDIDATES",
    "NOT_RECALLED_ABOVE_CLEARANCE",
    "NOT_RECALLED_ISOLATED",
    "NOT_RECALLED_TAINTED",
    "NOT_RECALLED_UNREADABLE",
    "recall_handoff",
]


async def recall_handoff(deps: QueenDeps, cell_id: CellId, task: Task) -> HandoffRef | None:
    """Return the newest Handoff the Queen can hold for `task`, written on `cell_id`.

    Args:
        deps: The Queen's collaborators; `honey` is read, `memory` and `trail` are written.
        cell_id: The Cell the task's bee worked on, whose Warden keeps the store the Queen cannot
            read. A Handoff counts only if it was gathered there.
        task: The task being resumed or taken back; its clearance is the most a Handoff may be
            labelled.

    Returns:
        The reference the task's next placement resumes from: the one she holds already when no
        Handoff newer than it may be used, or the checkpoint she filed just now from the newest
        that may. None when she holds nothing at all.
    """
    held = await latest_handoff(deps, task.id)
    if deps.honey is None:
        return held  # No Honey Store is wired, so nothing else can hold a copy.
    store = deps.honey.store
    refused: str | None = None
    for nectar in await store.handoff_nectars(task.id, cell_id, MAX_CANDIDATES):
        if held is not None and nectar.observed_at <= held.written_at:
            break  # She holds one at least this new, and every row after it is older.
        outcome = await _recall_one(deps, store, cell_id, task, nectar)
        if not isinstance(outcome, str):
            return outcome
        log.info("swarm.handoff_not_recalled", task_id=task.id, nectar_id=nectar.id, reason=outcome)
        refused = refused or outcome  # The newest refusal is the one that explains the loss.
    if refused is not None:
        # A copy was there and could not be used, which is why the task redoes work it might not
        # have: the one decision here worth a line of its own.
        await record_event(
            deps, "queen.decided", task.id, reason="handoff_not_recalled", detail=refused
        )
    return held


async def _recall_one(
    deps: QueenDeps, store: HoneyStore, cell_id: CellId, task: Task, nectar: Nectar
) -> HandoffRef | str:
    """File `nectar`'s Handoff as a checkpoint of the Queen's own; its reference, or why not."""
    # The checkpoint's event is how the trail names it, so without one there is nothing to check
    # it against and nothing to say it was not tainted.
    if nectar.event_id is None:
        return NOT_RECALLED_UNREADABLE
    refusal = await _refusal(deps, cell_id, nectar.event_id, nectar.observed_at)
    if refusal is not None:
        return refusal
    handoff = await _read(store, nectar)
    if handoff is None:
        return NOT_RECALLED_UNREADABLE
    label = _filing_label(handoff, nectar)
    # The same rule `read_handoff` applies at the resume; refused here so that nothing above the
    # task's clearance is put into her memory on its account at all.
    if label.rank > task.spec.clearance.rank:
        return NOT_RECALLED_ABOVE_CLEARANCE
    # Filed as her own checkpoint: a new event id, her trail's `memory.checkpoint`, her Bee Bread.
    # The device's event stays the device's, and `latest_handoff` finds this one from now on.
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    ref = await write_checkpoint(handoff.model_copy(update={"clearance": label}), task.id, ctx)
    await record_event(
        deps,
        "queen.decided",
        task.id,
        reason="handoff_recalled",
        cell_id=cell_id,
        nectar_id=nectar.id,
        source_event_id=nectar.event_id,
        event_id=ref.event_id,
    )
    return ref


async def _refusal(
    deps: QueenDeps, cell_id: CellId, event_id: EventId, observed_at: datetime
) -> str | None:
    """Say why the Handoff checkpointed as `event_id` is suspect, or None when nothing is."""
    tainted = await _newest(deps, TAINTED_KIND, event_id)
    cleared = await _newest(deps, TAINT_CLEARED_KIND, event_id)
    if cleared is not None and (tainted is None or cleared.at > tainted.at):
        return None  # A judge's verdict outranks every other suspicion of it.
    if tainted is not None:
        return NOT_RECALLED_TAINTED
    if await _written_under_isolation(deps, cell_id, observed_at):
        return NOT_RECALLED_ISOLATED
    return None


async def _written_under_isolation(deps: QueenDeps, cell_id: CellId, observed_at: datetime) -> bool:
    """Whether `cell_id` is isolated now and `observed_at` is since its suspect instant."""
    record = await read_isolation(deps, cell_id)
    event = record.isolated
    if record.state is not IsolationState.ISOLATED or event is None:
        return False
    # The first evidence the Guard cited, or when the isolation began: from there on the Cell's
    # memory is labelled, by an order its Warden may never have been given.
    suspect = event.payload.get("suspect_at")
    since = datetime.fromisoformat(suspect) if isinstance(suspect, str) else event.at
    return observed_at >= since


async def _newest(deps: QueenDeps, kind: str, subject_id: str) -> PheromoneEvent | None:
    """Return the newest `kind` event about `subject_id` on the Queen's trail, or None."""
    query = TrailQuery(kind=kind, subject_id=subject_id, newest_first=True, limit=1)
    events = await deps.trail.query(query)
    return events[0] if events else None


async def _read(store: HoneyStore, nectar: Nectar) -> Handoff | None:
    """Return the Handoff `nectar` holds, or None when it is not a plain, unlabelled one."""
    try:
        handoff = Handoff.model_validate_json(await store.nectar_content(nectar.id))
    except (NectarNotFoundError, ValidationError):
        return None  # Gone since it was listed, or bytes and shape no bee wrote.
    # A bee writes every Handoff unlabelled, and the stores refuse one that arrives labelled.
    return handoff if handoff.tainted is None else None


def _filing_label(handoff: Handoff, nectar: Nectar) -> HoneyClearance:
    """Return the label to file `handoff` at: its own, raised to its Nectar's but for the floor."""
    if held_by_floor_alone(nectar):
        return handoff.clearance  # Only the Real Cell floor holds the Nectar up (module docstring).
    return raise_label(handoff.clearance, nectar.clearance)
