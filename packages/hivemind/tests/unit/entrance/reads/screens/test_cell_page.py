"""Test hivemind.entrance.reads.screens.cell_page: one Cell's page read from a small Hive.

The page joins the Cell, its Warden's bees (from the board), its placed tasks, its Cell Wax, the
open Alarms placed on it and its Warden's grants; the Exoskeleton flag follows the newest attach or
detach edge; an unknown Cell is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/cell_page.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance.screens import heartbeat, scene
from builders.forage import make_grant
from builders.memory import make_cell_wax
from builders.observation import after, make_event
from pydantic import JsonValue

from hivemind.entrance.errors import CellNotFoundError
from hivemind.entrance.reads.screens import read_cell_page
from hivemind.memory import WaxSeverity
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_cell_id, new_warden_id
from waggle.messages.supervision import WorkerState


async def test_the_page_joins_bees_tasks_wax_alarms_grants_and_the_exoskeleton_edge() -> None:
    clock = FakeClock()
    hive = scene(clock)
    cell_id, warden_id = hive.link.cell.id, hive.link.warden_id
    [task] = await hive.submit({"a": ()})
    await hive.place(task)
    hive.board.record(warden_id, heartbeat([(task.id, WorkerState.RUNNING)]), clock.now())
    await hive.write_wax(make_cell_wax(clock, cell_id=cell_id, severity=WaxSeverity.NOTE))
    payload = {"kind": "WORKER_FAILED", "severity": "WARNING", "task_id": task.id}
    await hive.record(make_event("alarm.raised", new_alarm_id(clock), clock.now(), payload=payload))
    grant = make_grant(clock=clock, holder=warden_id, cell_id=cell_id, task_id=task.id)
    await hive.deps.ledger.record_grant(grant)
    await hive.record(make_event("cell.exoskeleton_attached", cell_id, after(5)))
    await hive.record(make_event("cell.exoskeleton_detached", cell_id, after(9)))

    page = await read_cell_page(hive.reads, cell_id, clock.now())

    assert page.cell.id == cell_id and page.warden is not None
    assert [bee.role for bee in page.bees] == ["WARDEN", "DRONE"]
    assert [view.id for view in page.tasks] == [task.id]
    assert [w.severity for w in page.wax] == [WaxSeverity.NOTE]
    assert len(page.alarms) == 1
    assert page.forage is not None and [g.id for g in page.forage.grants] == [grant.id]
    assert page.exoskeleton_attached is False
    assert page.last_patrol_at is None


async def test_the_last_patrol_is_the_newest_patrol_of_the_cells_own_warden() -> None:
    clock = FakeClock()
    hive = scene(clock)
    cell_id, warden_id = hive.link.cell.id, hive.link.warden_id
    other = new_warden_id(clock)
    skipped: dict[str, JsonValue] = {"outcome": "SKIPPED", "observations": 3, "signals": 0}
    await hive.record(make_event("warden.patrol", warden_id, after(10), payload=skipped))
    await hive.record(make_event("warden.patrol", warden_id, after(70), payload=skipped))
    await hive.record(make_event("warden.patrol", other, after(500), payload=skipped))

    page = await read_cell_page(hive.reads, cell_id, clock.now())

    assert page.last_patrol_at == after(70)  # Any outcome counts; another Warden's does not.


async def test_an_unknown_cell_is_refused() -> None:
    hive = scene(FakeClock())

    with pytest.raises(CellNotFoundError):
        await read_cell_page(hive.reads, new_cell_id(FakeClock()), hive.clock.now())
