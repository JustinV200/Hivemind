"""Record the Queen's tool, Basket and integrity events on her trail, with her own identity.

The Queen's tool work writes three families of event: `tool.*` (the stages of a request, a sandbox,
an announcement), `basket.*` (an offer, a shipment, a receipt, a refusal) and one `guard.*` kind
(`guard.basket_integrity_failed`, bytes that did not hash to what was offered). Each is minted by
the family's own builder (`hivemind.royal_jelly.promotion.events.tool_event`,
`hivemind.basket.events.basket_event`) under the identity `QueenDeps.identity` names; this module
only supplies that identity and the one place the three are written, so no handler repeats it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by every sibling module that records; calls into `hivemind.basket.events`, `hivemind.cell`
    (CellIdentity), `hivemind.pheromone` (GuardEvent) and `hivemind.royal_jelly.promotion.events`.

Key invariants:
    - A payload carries ids, hashes, sizes, enum values and short reasons, never blob bytes, a
      package's source or a spec's description.
    - A hash on the trail is the full digest for a Basket event and its first sixteen digits for a
      tool event, as each family's own builders write them.

See Also:
    - hivemind.royal_jelly.promotion.events for the tool events the promotion flow writes itself.
    - hivemind.basket.events for the Basket's own events.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from hivemind.basket.events import EventStamp, basket_event
from hivemind.basket.transfer import IntegrityFailure
from hivemind.cell import CellIdentity
from hivemind.pheromone import GuardEvent
from hivemind.queen.deps import QueenDeps
from hivemind.royal_jelly.promotion.events import PayloadValue, ToolIdentity, tool_event
from waggle.ids import CellId, ToolId, WardenId, new_event_id

__all__ = ["record_basket", "record_integrity_failure", "record_tool", "tool_identity"]


def tool_identity(deps: QueenDeps) -> ToolIdentity:
    """Return the Hive, node and actor the Queen's `tool.*` events are recorded under."""
    identity = deps.identity
    return ToolIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor)


async def record_tool(deps: QueenDeps, kind: str, tool_id: ToolId, **payload: PayloadValue) -> None:
    """Record one `tool.*` event about `tool_id`.

    Args:
        deps: The Queen's collaborators; `trail`, `clock` and `identity` are used.
        kind: A `ToolEvent` kind (`"tool.sandbox_provisioned"`, `"tool.announced"`, ...).
        tool_id: The tool the event is about; becomes its subject.
        **payload: Ids, hashes, enum values and short reasons.
    """
    await deps.trail.record(tool_event(tool_identity(deps), deps.clock, kind, tool_id, **payload))


async def record_basket(
    deps: QueenDeps, kind: str, subject: CellId, payload: Mapping[str, JsonValue]
) -> None:
    """Record one `basket.*` event about the Cell a blob came from or went to.

    Args:
        deps: The Queen's collaborators.
        kind: A `BasketEvent` kind (`"basket.offered"`, `"basket.shipped"`, ...).
        subject: The origin or destination Cell's id.
        payload: Hashes, sizes, enum values and short reasons; never bytes.
    """
    identity = deps.identity
    stamp = EventStamp(
        CellIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor),
        deps.clock,
    )
    await deps.trail.record(basket_event(stamp, kind, subject, payload))


async def record_integrity_failure(
    deps: QueenDeps, warden_id: WardenId, failure: IntegrityFailure
) -> None:
    """Record `guard.basket_integrity_failed` for a transfer that did not verify.

    Args:
        deps: The Queen's collaborators.
        warden_id: The Warden that sent the offer or the chunks.
        failure: What failed (`manifest` signature or `whole` hash) and how far the bytes got.
    """
    identity = deps.identity
    await deps.trail.record(
        GuardEvent(
            id=new_event_id(deps.clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=deps.clock.now(),
            actor=identity.actor,
            kind="guard.basket_integrity_failed",
            subject_id=warden_id,
            payload={
                "sha256": failure.sha256,
                "stage": failure.stage,
                "received_bytes": failure.received_bytes,
            },
        )
    )
