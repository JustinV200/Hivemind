"""Define record_event: the one place the Queen builds and records a queen.* QueenEvent.

Codingrules section 12: every trail event carries "ids, counts and enums, never text", and
`subject_id` is "the thing the event is about" -- the hive id for most `queen.*` kinds, or the
task id for `queen.assigned` and every task-scoped `queen.decided`. `record_event` is the one
function every module under `hivemind.queen` that writes a `queen.*` event calls, so the shape
(minted id, this Queen's own identity, `subject_id`, a payload of ids/enums/counts only) is built
in exactly one place rather than once per caller.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen`, `hivemind.queen.dispatcher` and `hivemind.queen.ticks.alarms`,
    every time one of the four `queen.*` kinds this phase adds (`decided`, `planned`, `assigned`,
    `awake`) is recorded. Calls into `hivemind.pheromone` (QueenEvent), `hivemind.queen.deps`
    (QueenDeps) and pydantic only.

Key invariants:
    - `payload` never carries a string over `hivemind.pheromone.events.base`'s own per-string
      length bound: every caller passes ids, enum values and counts only (codingrules section 12).

See Also:
    - .claude/codingrules.md section 12 for the trail-event shape this function builds.
    - hivemind.pheromone for QueenEvent, the class this function constructs.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.pheromone import QueenEvent
from hivemind.queen.deps import QueenDeps
from waggle.ids import new_event_id

__all__ = ["record_event"]


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
