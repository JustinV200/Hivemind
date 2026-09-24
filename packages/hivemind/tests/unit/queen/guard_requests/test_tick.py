"""Tests for a Guard request on the running Queen's tick: decided there, and after a restart too.

Roadmap step 10.6a (ADR-0035). The running Queen is the Guard Bee's door: filing wakes her, and
her next tick drains the request as a GUARD_REQUEST item and decides it. The request is durable
before the filing returns (her SQLite table on the Hive's file), so one filed just before a restart
is still pending in a fresh Queen over a fresh connection, and the first tick she runs (the Hive
Stand Warden's next heartbeat starts one) decides it.

Fits into the Hive:
    Mirrors the GUARD_REQUEST wiring in src/hivemind/queen/queen.py and the SQLite table in
    src/hivemind/queen/guard_requests/sqlite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/contracts/test_guard_request_store_contract.py for the table's own contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from builders.isolation import make_guard_report
from builders.queen import make_queen_deps
from builders.supervision import make_telemetry

from hivemind.common.sqlite import connect
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.queen.guard_requests import GuardDeps, SqliteGuardRequestStore
from hivemind.queen.isolation import ISOLATED_KIND
from waggle.clock import FakeClock
from waggle.messages.supervision import Heartbeat, WardenState

_TIMEOUT_S = 5.0  # Bound on stopping a Queen's loop in a test.


def _heartbeat() -> Heartbeat:
    """A Warden's routine heartbeat: what starts a Queen's tick with nothing else to do."""
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def _until(condition: Callable[[], Awaitable[bool]], limit: int = 2_000) -> None:
    """Yield the event loop until the async `condition()` holds, or fail."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("The condition never held.")


async def test_the_running_queen_decides_a_filed_request_on_the_tick_it_wakes() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=GuardDeps(pause_timeout_s=0.0))
    queen = Queen(deps)
    await queen.attach_warden(link)
    run = asyncio.ensure_future(queen.run())
    report = make_guard_report(clock, cell_id=link.cell.id)

    await queen.file_guard_request(report)  # Wakes her: no Warden message is needed.
    await _until(lambda: _decided(deps.guard, report.id))

    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND, subject_id=link.cell.id))
    await queen.stop()
    await asyncio.wait_for(run, timeout=_TIMEOUT_S)
    await warden_end.close()


async def test_a_request_filed_before_a_restart_is_decided_after_it(tmp_path: Path) -> None:
    clock = FakeClock()
    db = tmp_path / "hive.db"
    before = await SqliteGuardRequestStore.create(connect(db), clock)
    deps, link, warden_end = make_queen_deps(
        clock, guard=GuardDeps(requests=before, pause_timeout_s=0.0)
    )
    report = make_guard_report(clock, cell_id=link.cell.id)
    await Queen(deps).file_guard_request(report)  # Durable before it returns; then she stops.
    await warden_end.close()

    # A new process: a fresh connection to the same file, a new Queen, the same Cell's Warden.
    after = await SqliteGuardRequestStore.create(connect(db), clock)
    restarted, again, again_end = make_queen_deps(
        clock, cell=link.cell, guard=GuardDeps(requests=after, pause_timeout_s=0.0)
    )
    queen = Queen(restarted)
    await queen.attach_warden(again)
    run = asyncio.ensure_future(queen.run())
    await again_end.send(_heartbeat())  # The Warden's next heartbeat starts her first tick.
    await _until(lambda: _decided(restarted.guard, report.id))

    decided = await restarted.trail.query(TrailQuery(kind="queen.decided"))
    assert [event.payload["report_id"] for event in decided] == [report.id]
    await queen.stop()
    await asyncio.wait_for(run, timeout=_TIMEOUT_S)
    await again_end.close()


async def _decided(guard: GuardDeps, report_id: str) -> bool:
    """Whether the request carrying `report_id` has the Queen's decision stamped on it."""
    row = await guard.requests.get(report_id)
    return row is not None and row.decision is not None
