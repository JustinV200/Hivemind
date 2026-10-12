"""Build the Basket's Pheromone Trail events: ids, hashes, sizes and labels, never content.

Every `basket.*` event is a `BasketEvent` (`hivemind.pheromone`). The deposit door builds them but
never records them (it holds no trail): it returns them for its caller to record, or attaches them
to the error it raises. This module is the one place that stamps an event with the Hive, node and
actor, so every Basket event is shaped the same, and the one place that decides what a blob
contributes to a payload: its hash, size, kind, label and provenance ids, and never its bytes, its
title or its summary (codingrules section 12).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Called by
    `hivemind.basket.deposits`; later steps call it for `basket.offered`, `basket.received`,
    `basket.evicted` and the rest. Calls into `hivemind.cell` (CellIdentity) and
    `hivemind.pheromone` only.

Key invariants:
    - A payload carries only scalars, ids and short enum values; the trail's own validator also
      refuses a `content`, `text` or `output` key, so a slip would fail loudly.
    - `basket_event` stamps the id and time from the injected clock, or the id it is given (the
      deposit event's id is minted first so the blob can name it); `EventStamp` carries who/when.

See Also:
    - hivemind.pheromone.events.families.resources.BasketEvent for the event kinds.
    - hivemind.honey_store.identity.honey_event, the shape this mirrors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.basket.models import Blob
from hivemind.cell import CellIdentity
from hivemind.pheromone import BasketEvent
from waggle.clock import Clock
from waggle.ids import EventId, new_event_id

__all__ = ["EventStamp", "basket_event", "blob_facts"]


@dataclass(frozen=True, slots=True)
class EventStamp:
    """Who is recording and when: the Hive, node and actor, and the clock that times them."""

    identity: CellIdentity  # The Hive, node and actor every event is stamped with.
    clock: Clock  # Supplies each event's `at` and, unless given, its id.


def basket_event(
    stamp: EventStamp,
    kind: str,
    subject_id: str,
    payload: Mapping[str, JsonValue],
    event_id: EventId | None = None,
) -> BasketEvent:
    """Mint one BasketEvent with a fresh id (or the given one), the clock's time and a payload.

    Args:
        stamp: The Hive, node, actor and clock the event is recorded under.
        kind: One of `BasketEvent`'s own kinds (`"basket.deposited"`, ...).
        subject_id: The id the event is about: the origin or destination Cell's id.
        payload: Ids, hashes, sizes and labels only; never bytes, titles or summaries.
        event_id: The id to use, when the caller already named the event (a blob's
            `deposit_event_id`); a fresh one otherwise.

    Returns:
        The validated event, ready for the caller to record on its trail.

    Raises:
        pydantic.ValidationError: `kind` is not a basket.* kind or the payload is refused.
    """
    return BasketEvent(
        id=event_id if event_id is not None else new_event_id(stamp.clock),
        hive_id=stamp.identity.hive_id,
        node_id=stamp.identity.node_id,
        at=stamp.clock.now(),
        actor=stamp.identity.actor,
        kind=kind,
        subject_id=subject_id,
        payload=dict(payload),
    )


def blob_facts(blob: Blob) -> dict[str, JsonValue]:
    """Return the payload facts every event about a stored blob carries.

    Args:
        blob: The blob the event is about.

    Returns:
        Its hash, size, kind, type, label, taint flag and platform tags: nothing derived from its
        bytes or from its Honey text.
    """
    return {
        "sha256": blob.sha256,
        "size_bytes": blob.size_bytes,
        "media_kind": blob.media_kind.value,
        "media_type": blob.media_type,
        "clearance": blob.clearance.value,
        "tainted": blob.tainted,
        "platform_tags": [tag.value for tag in blob.platform_tags],
    }
