"""Tests for the Queen's own ISOLATE policy row: an escalated Alarm isolates the Cell it names.

Roadmap step 10.6a (ADR-0043). `PolicyAction.ISOLATE` is the Queen's alone (a Warden's policy
never loads it); in her escalation policy it maps to `QueenAction.ISOLATE_CELL`, which records her
decision and isolates the Alarm's Cell through the one isolation path. An Alarm that names no
Cell, or names the Hive Stand's own (the human's alone), reaches the human instead.

Fits into the Hive:
    Mirrors the ISOLATE_CELL branch of src/hivemind/queen/ticks/alarms.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.isolation for the one path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.isolation import hive_stand_cell
from builders.queen import WardenEnd, make_queen_deps

from hivemind.cell import Cell
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.isolation import ISOLATED_KIND
from hivemind.queen.queen import Queen
from hivemind.supervision import EscalationPolicy, PolicyAction
from waggle.clock import FakeClock
from waggle.ids import CellId, new_alarm_id, new_event_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised

_TIMEOUT_S = 5.0
# A task-less Alarm is decided at attempts 0, which no row matches: the default is the row here.
_ISOLATE = EscalationPolicy(rules=(), default=PolicyAction.ISOLATE)


def _alarm(deps: QueenDeps, cell_id: CellId | None) -> AlarmRaised:
    """A SECURITY Alarm about `cell_id`, naming the trail event that explains it."""
    return AlarmRaised(
        alarm_id=new_alarm_id(deps.clock),
        kind=AlarmKind.SECURITY,
        severity=AlarmSeverity.CRITICAL,
        origin=new_worker_id(deps.clock),
        attempts=0,
        raised_at=deps.clock.now(),
        context=AlarmContext(
            task_id=None,
            cell_id=cell_id,
            worker_id=None,
            event_id=new_event_id(deps.clock),
            handoff=None,
        ),
        detail="A scanner correlation flagged this Cell.",
        clearance=WireHoneyClearance.C1,
        reason="Security events always go up.",
    )


async def _running(
    cell: Cell | None = None,
) -> tuple[Queen, QueenDeps, WardenEnd, asyncio.Task[None]]:
    """A running Queen on the ISOLATE policy, with one Warden attached."""
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(
        clock, cell=cell, policy=_ISOLATE, guard=GuardDeps(pause_timeout_s=0.0)
    )
    queen = Queen(deps)
    await queen.attach_warden(link)
    return queen, deps, warden_end, asyncio.ensure_future(queen.run())


async def _until(condition: Callable[[], Awaitable[bool]], limit: int = 2_000) -> None:
    """Yield the event loop until the async `condition()` holds, or fail."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("The condition never held.")


async def _stop(queen: Queen, run: asyncio.Task[None], warden_end: WardenEnd) -> None:
    await queen.stop()
    await asyncio.wait_for(run, timeout=_TIMEOUT_S)
    await warden_end.close()


async def test_an_isolate_row_isolates_the_alarms_cell_after_her_decision() -> None:
    queen, deps, warden_end, run = await _running()
    cell_id = queen.wardens[0].cell.id
    alarm = _alarm(deps, cell_id)

    await warden_end.send(alarm)
    await _until(lambda: _has(deps, ISOLATED_KIND))

    [decided] = await deps.trail.query(TrailQuery(kind="queen.decided", subject_id=cell_id))
    assert (decided.payload["action"], decided.payload["alarm_id"]) == (
        "ISOLATE_CELL",
        alarm.alarm_id,
    )
    [isolated] = await deps.trail.query(TrailQuery(kind=ISOLATED_KIND))
    assert isolated.payload["evidence"] == [alarm.context.event_id]
    handled = await deps.trail.query(TrailQuery(kind="alarm.handled", subject_id=alarm.alarm_id))
    assert [event.payload.get("action") for event in handled] == ["ISOLATE_CELL"]
    await _stop(queen, run, warden_end)


async def test_an_isolate_row_on_the_hive_stand_reaches_the_human_instead() -> None:
    queen, deps, warden_end, run = await _running(hive_stand_cell())

    await warden_end.send(_alarm(deps, queen.wardens[0].cell.id))
    await _until(lambda: _escalated(queen))

    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND)) == ()
    assert await deps.trail.query(TrailQuery(kind="guard.denied"))
    await _stop(queen, run, warden_end)


async def test_an_alarm_naming_no_cell_reaches_the_human_instead() -> None:
    queen, deps, warden_end, run = await _running()

    await warden_end.send(_alarm(deps, None))
    await _until(lambda: _escalated(queen))

    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND)) == ()
    await _stop(queen, run, warden_end)


async def _has(deps: QueenDeps, kind: str) -> bool:
    return bool(await deps.trail.query(TrailQuery(kind=kind)))


async def _escalated(queen: Queen) -> bool:
    return bool(queen.human_inbox.alarms)
