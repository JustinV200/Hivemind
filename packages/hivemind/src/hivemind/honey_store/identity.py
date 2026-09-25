"""Mint every Honey Store trail event in one place, stamped with the writer's own identity.

The Honey Store (the Hive's cold-tier knowledge base: raw Nectar deposits ripened into searchable
Honey) records a `HoneyEvent` on the Pheromone Trail (the Hive's append-only audit log) for every
deposit, ripening, label change and query. Intake, the Ripener and the retriever all need the same
three things to do that -- which Hive, which node, which actor -- plus a fresh event id and the
injected clock's time, so `HoneyIdentity` carries the first three and `honey_event` is the single
function that turns them into an event. Keeping minting here means the payload rule (ids, counts,
enum values and booleans only, never a deposit's content, a title or a query's words) is stated
and typed once instead of once per caller.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by `hivemind.honey_store.nectar` (intake), `hivemind.honey_store.ripening` (the Ripener) and
    `hivemind.honey_store.honey` (retrieval); built once by the composition root. Calls into
    `hivemind.pheromone` (HoneyEvent) and `waggle` (ids, clock) only.

Key invariants:
    - Every payload value is a scalar (`str`, `int`, `float`, `bool` or None): the type admits no
      list or mapping a caller could smuggle content into, and `HoneyEvent`'s own validator still
      refuses the forbidden keys (`content`, `text`, ...) and any string over its length cap.
    - `actor` must be a valid `PheromoneEvent.actor` (a hive_/warden_/worker_/device_ id, or
      "human"/"system"); it is checked when the first event is minted, not at construction.
    - Every event gets a fresh id and `at = clock.now()` from the clock the caller passes, never a
      wall-clock read of its own.

See Also:
    - .claude/codingrules.md section 12 for the trail rules every honey.* event follows.
    - hivemind.pheromone.events.families for HoneyEvent and the honey.* kinds it accepts.
    - hivemind.memory.context.MemoryIdentity for the same identity shape on the memory side.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.pheromone import HoneyEvent
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, new_event_id

__all__ = ["HoneyIdentity", "PayloadValue", "honey_event"]

# One payload value: an id, a count, an enum's `.value` or a flag. Deliberately no list or mapping,
# so no caller can nest a deposit's text inside an event (codingrules section 12).
type PayloadValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class HoneyIdentity:
    """The Hive, node and actor stamped on every HoneyEvent one Honey Store writer records.

    Attributes:
        hive_id: The Hive the events belong to.
        node_id: The node (the Queen's own, in practice) whose trail segment the events land on.
        actor: Who acts: a bee id, or the literal "human" or "system"
            (`hivemind.pheromone.events.base.ACTOR_LITERALS`); the House Bee and intake run as
            "system" unless the composition root names a bee.
    """

    hive_id: HiveId
    node_id: NodeId
    actor: str


def honey_event(
    identity: HoneyIdentity, clock: Clock, kind: str, subject_id: str, /, **payload: PayloadValue
) -> HoneyEvent:
    """Mint one HoneyEvent with a fresh id, the clock's current time and a scalar payload.

    The four leading parameters are positional-only so a payload key may share a name with one
    of them (a `kind` or `subject_id` key in the payload never collides with the parameter).

    Args:
        identity: Which Hive, node and actor the event is recorded under.
        clock: The injected clock; supplies both the event id's timestamp and `at`.
        kind: One of HoneyEvent's own kinds (`"honey.ripened"`, ...).
        subject_id: The id the event is about (a Nectar, Honey, Cell or bee id); any waggle id
            kind is accepted.
        **payload: Ids, counts, enum values and flags only; never content, titles or query text.

    Returns:
        The validated HoneyEvent, ready to hand to a store write or `HoneyStore.record`.

    Raises:
        pydantic.ValidationError: `kind` is not a honey.* kind, `subject_id` or `identity.actor`
            is not a well-formed id, or the payload uses a forbidden key.

    Example:
        `honey_event(identity, clock, "honey.ripened", nectar.id, rows=3, summarised=True)`
        records that one Nectar ripened into three rows, with no text in the payload.
    """
    # Copied into a JsonValue-typed dict: HoneyEvent.payload is dict[str, JsonValue], and dict is
    # invariant in its value type, so the narrower scalar mapping cannot be passed as it is.
    fields: dict[str, JsonValue] = dict(payload)
    return HoneyEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor=identity.actor,
        kind=kind,
        subject_id=subject_id,
        payload=fields,
    )
