"""Define record_event and record_forage_event: the one place the Queen builds each trail event.

Codingrules section 12: every trail event carries "ids, counts and enums, never text", and
`subject_id` is "the thing the event is about" -- the hive id for most `queen.*` kinds, or the
task id for `queen.assigned` and every task-scoped `queen.decided`. `record_event` is the one
function every module under `hivemind.queen` that writes a `queen.*` event calls, so the shape
(minted id, this Queen's own identity, `subject_id`, a payload of ids/enums/counts only) is built
in exactly one place rather than once per caller. `record_forage_event` is its forage-family
sibling (roadmap step 4.7): `hivemind.queen.dispatcher._record_forage_granted` already builds a
`hivemind.pheromone.ForageEvent` by hand because no such helper existed yet (that dispatch's own
flagged gap); `hivemind.queen.forage.grants` and `.requests` are this helper's own two callers, so
every `forage.*` kind they write shares one shape too, the same way `record_event` already does
for `queen.*`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen`, `hivemind.queen.dispatcher` and `hivemind.queen.ticks.alarms`
    (`record_event`), and by `hivemind.queen.forage.grants` and `.requests`
    (`record_forage_event`). Calls into `hivemind.pheromone` (QueenEvent, ForageEvent),
    `hivemind.queen.deps` (QueenDeps) and pydantic only.

Key invariants:
    - `payload` never carries a string over `hivemind.pheromone.events.base`'s own per-string
      length bound: every caller passes ids, enum values and counts only (codingrules section 12).

See Also:
    - .claude/codingrules.md section 12 for the trail-event shape these functions build.
    - hivemind.pheromone for QueenEvent and ForageEvent, the classes these functions construct.
    - hivemind.queen.dispatcher for _record_forage_granted, the hand-built forage.granted call
      record_forage_event now lets hivemind.queen.forage share the same shape with, without this
      dispatch reaching into a file it does not own to change that one, too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.pheromone import ForageEvent, QueenEvent
from waggle.ids import new_event_id

if TYPE_CHECKING:
    # Only for the type hints below: hivemind.queen.forage.grants now calls record_forage_event
    # (roadmap step 4.7), and hivemind.queen.deps.QueenDeps carries a ForageLedger field whose own
    # package (hivemind.queen.forage) that module sits inside, so a real (non-TYPE_CHECKING)
    # import here would cycle back through queen/forage/__init__.py -> grants.py -> this module.
    from hivemind.queen.deps import QueenDeps

__all__ = ["record_event", "record_forage_event"]


async def record_event(deps: QueenDeps, kind: str, subject_id: str, **payload: JsonValue) -> None:
    """Build and record one queen.* QueenEvent, subject to `subject_id`.

    Args:
        deps: The Queen's collaborators; `trail`, `clock` and `identity` are what this writes with.
        kind: One of `hivemind.pheromone.QueenEvent.KINDS`.
        subject_id: The thing this event is about: the hive id, or a task id.
        **payload: Ids, enum values and counts only (codingrules section 12); never free text.
    """
    event = QueenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=subject_id,
        payload=payload,
    )
    await deps.trail.record(event)


async def record_forage_event(
    deps: QueenDeps, kind: str, subject_id: str, **payload: JsonValue
) -> None:
    """Build and record one forage.* ForageEvent, subject to `subject_id`.

    Args:
        deps: The Queen's collaborators; `trail`, `clock` and `identity` are what this writes with.
        kind: One of `hivemind.pheromone.ForageEvent.KINDS` (`forage.requested`, `.granted`,
            `.denied`, `.revoked`, `.expired`, ...).
        subject_id: The thing this event is about: normally a `GrantId`.
        **payload: Ids, enum values and counts only (codingrules section 12); never free text.
    """
    event = ForageEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=subject_id,
        payload=payload,
    )
    await deps.trail.record(event)
