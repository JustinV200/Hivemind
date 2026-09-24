"""Label memory tainted: taint_memory, the one function that ever sets the taint label.

Roadmap step 10.6d (ADR-0035): "Taint is one label with three setters and one clearer." The three
setters are callers, not code paths: the Queen isolating a Cell (10.6a), the one quarantine
intervention (10.6c), and the Queen acting on a Guard report about a Honey item (10.6, phase 7).
Each calls `taint_memory` with its closed `TaintSource`, a `TaintScope` naming the slice of memory
that is suspect, and a `TaintStamp` saying why and which trail event justified it. For every item
the scope covers that is not already TAINTED (unlabelled, or CLEARED by an earlier verdict), this
mints a `memory.tainted` event and writes the TAINTED marker carrying that event's id, the two in
one transaction (`TaintLedger.write_taint`), so the label and its audit row can never disagree. The
memory tables are always searched; a caller passes further ledgers (the Honey Store for Nectar and
Honey, from phase 7) to reach items that live elsewhere. From then on `memory.assemble`, retrieval
and every Handoff loader refuse those items until a judge clears them.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. Called by
    the three setters above and by nothing else (tests/unit/memory/taint/test_only_setter.py holds
    every module to that). Calls into `hivemind.common.logging`, `hivemind.memory.context`,
    `hivemind.memory.errors`, `hivemind.pheromone` (MemoryEvent), this package's `ledger`,
    `marker` and `scope`, and waggle.

Key invariants:
    - Every label written here is TAINTED and carries the id of the `memory.tainted` event
      recorded with it, in the same transaction.
    - An item already TAINTED is never relabelled: its first marker, and the event that set it,
      stay as they are.
    - Event payloads carry the item's kind, the source, the bounded reason and the justifying event
      id; never any of the item's content.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md, "Taint is one
      label with three setters and one clearer".
    - hivemind.memory.taint.clear for clear_taint, the one clearer.
    - hivemind.memory.taint.scope for the scope shapes the two next callers use.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.common.logging import get_logger
from hivemind.memory.context import MemoryContext
from hivemind.memory.errors import InvalidTaintTransitionError
from hivemind.memory.taint.ledger import TaintLedger
from hivemind.memory.taint.marker import (
    MAX_TAINT_REASON_CHARS,
    TaintMarker,
    TaintSource,
    TaintState,
    TaintTarget,
)
from hivemind.memory.taint.scope import TaintScope
from hivemind.pheromone import MemoryEvent
from waggle.ids import new_event_id
from waggle.messages.base import EventIdField

TAINTED_KIND = "memory.tainted"  # The trail kind every label written here records.

log = get_logger(__name__)

__all__ = ["TAINTED_KIND", "TaintReport", "TaintStamp", "taint_memory"]


class TaintStamp(BaseModel):
    """Why a slice of memory is being labelled: the setter, its reason and its evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: TaintSource = Field(description="Which of the three setters is labelling.")
    reason: str = Field(
        min_length=1,
        max_length=MAX_TAINT_REASON_CHARS,
        description="Why, naming ids only (the report, the Cell, the episode), never content.",
    )
    cause_event_id: EventIdField | None = Field(
        default=None,
        description="The trail event that justified it (cell.isolated, warden.intervened, "
        "guard.alert); recorded on every memory.tainted event this stamp writes.",
    )


class TaintReport(BaseModel):
    """What one `taint_memory` call labelled, in the order it labelled them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tainted: tuple[TaintTarget, ...] = Field(description="Every item newly labelled TAINTED.")


async def taint_memory(
    scope: TaintScope,
    stamp: TaintStamp,
    ctx: MemoryContext,
    extra_ledgers: Sequence[TaintLedger] = (),
) -> TaintReport:
    """Label every item `scope` covers TAINTED, each with its own `memory.tainted` event.

    Args:
        scope: Which bees', tasks' and kinds' items, from when (`TaintScope.for_bee`,
            `TaintScope.for_cell`).
        stamp: The setter's source, its reason and the event that justified it.
        ctx: The memory tables (always searched), the identity events are stamped with, and the
            clock that mints them.
        extra_ledgers: Further ledgers to search: the Honey Store for Nectar and Honey from phase
            7; empty today.

    Returns:
        Every item newly labelled; an item already TAINTED is left alone and not reported.

    Raises:
        Whatever a ledger's store or the trail raises: a label is never written unrecorded.
    """
    labelled: list[TaintTarget] = []
    # The memory tables first, then whatever other store the caller named, each asked the same
    # question: what does this scope cover that is not already refused?
    for ledger in (ctx.store, *extra_ledgers):
        for target in await ledger.find_taintable(scope):
            if await _label(ledger, target, stamp, ctx):
                labelled.append(target)
    return TaintReport(tainted=tuple(labelled))


async def _label(
    ledger: TaintLedger, target: TaintTarget, stamp: TaintStamp, ctx: MemoryContext
) -> bool:
    """Write one TAINTED marker and its event; False if a concurrent setter got there first."""
    event = _tainted_event(target, stamp, ctx)
    marker = TaintMarker(
        state=TaintState.TAINTED,
        source=stamp.source,
        reason=stamp.reason,
        event_id=event.id,
        at=event.at,
    )
    try:
        await ledger.write_taint(target, marker, event)
    except InvalidTaintTransitionError:
        # Found untainted a moment ago, tainted now: another setter labelled it in between, and
        # its first marker must stand (module docstring). Nothing was written for this item.
        log.debug("memory.taint.already_tainted", item_id=target.item_id, kind=target.kind.value)
        return False
    return True


def _tainted_event(target: TaintTarget, stamp: TaintStamp, ctx: MemoryContext) -> MemoryEvent:
    """Build one item's `memory.tainted` event: its kind, the source, the reason, the cause."""
    payload: dict[str, JsonValue] = {
        "item_kind": target.kind.value,
        "source": stamp.source.value,
        "reason": stamp.reason,
    }
    if stamp.cause_event_id is not None:
        payload["cause_event_id"] = stamp.cause_event_id
    return MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind=TAINTED_KIND,
        subject_id=target.item_id,
        payload=payload,
    )
