"""End-to-end: a slow Virtual Cell provision raises no false Alarm; the Cell's calls are audited.

A real run (2026-09-24, a goal placed on a Docker Virtual Cell) found two defects this module
proves closed through a whole Hive: the Queen, the Hive Stand's own Warden, `CellListener`, and a
real in-Cell Warden per Virtual Cell over `builders.virtual_cells.ContainerSpawningFakeCellBackend`,
built exactly as `tests.e2e.test_phase10_exit_criteria`'s criterion 1b builds its Hive. The fake
backend's own `set_provision_delay` slows every provision far past the manifest's liveness window
(three misses of a 0.1 s heartbeat: 0.3 s), the same shape as a Docker provision that took 5.6 s
against a 3 s window. First, the Hive Stand's Warden, heartbeating the whole time, must never be
reported unreachable: the Queen's stall is her own, not the Warden's. Second, the Drone inside the
Cell calls the model through the Cell's own Fanner, so at least one `llm.call` recorded under the
Cell's own node id reaches the Queen's trail once the Cell's segment is shipped.

The dispatcher lifecycle fix adds a second scenario over the same Hive: a goal withdrawn while its
Cell is still being provisioned. Her tick must keep handling the Hive Stand's Heartbeats all
through the provision (it no longer awaits one), `cell.provisioning` must be on the trail while the
backend is still at work, and once the Cell lands it must be destroyed rather than left behind for
a task that will never run there.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.inbox.links for the per-link readers the Queen now hears every Warden through.
    - hivemind.queen.ticks.liveness for record_heartbeat's never-back-online-on-a-stale rule.
    - hivemind.cli.in_cell.fanner for the per-Cell Fanner that records the Cell's own llm.call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.brood_chamber import TaskFilter, TaskStatus, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.chat import MAX_CHAT_PAGE, ChatKind, ChatQuery
from hivemind.supervision import AlarmKind
from waggle.clock import SystemClock
from waggle.ids import TaskId, WardenId

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # Generous: the scenario finishes in a few seconds, the delay included.
# The Hive Stand's own cadence: a 0.3 s window (three misses), wide enough that scheduling jitter
# in a busy test process never looks like silence, narrow enough for the stall to dwarf it.
_HEARTBEAT_INTERVAL_S = 0.1
# Fifteen heartbeats against a window of three: every Heartbeat the Hive Stand's Warden sends
# during the stall but the last few is stale by the time the Queen gets to it.
_PROVISION_DELAY_S = 1.5
# Five Heartbeat intervals: far more than one tick handled before a dispatch pass blocks, far less
# than the provision takes, so only a tick that never waits on the provision gets this far in time.
_HEARD_THROUGH_A_PROVISION = timedelta(seconds=0.5)


def test_a_slow_provision_raises_no_false_alarm_and_ships_the_cells_llm_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provision slower than the liveness window: the goal succeeds, no false Alarm, calls land.

    `overwinter_enabled=False` so the Cell is torn down once its task finishes: its Warden stops,
    ships its last trail segment, and only then is `cell.destroyed` recorded (`hivemind.queen.
    cell_gate.quiesce`), which is what makes the Cell's own `llm.call` rows readable here.
    """
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    tuning = VirtualCellsTuning(
        prefer="virtual", overwinter_enabled=False, heartbeat_interval_s=_HEARTBEAT_INTERVAL_S
    )
    manifest_path = virtual_cells_manifest(tmp_path, tuning=tuning)
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_run_slow_provision(hive))


async def _run_slow_provision(hive: Hive) -> None:
    """Run one goal through a slowed provision, then read the chat, the inbox and the trail."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    supervision = hive.manifest.supervision
    # The stall must outlast the window, or this test proves nothing about a stalled tick.
    assert supervision.heartbeat_interval_s * supervision.heartbeat_miss_limit < _PROVISION_DELAY_S
    backend.set_provision_delay(_PROVISION_DELAY_S)
    try:
        async with run_hive(hive):
            goal_id = await hive.queen.submit_goal(
                "write one haiku about bees", clearance=HoneyClearance.C1
            )
            await wait_until(lambda: _goal_finished(hive, goal_id), timeout_s=_TIMEOUT_S)
            [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
            cell_id = _cell_id_for_task(await hive.stores.trail.query(TrailQuery()), task.id)
            # The Cell's own Warden ships its last segment before the Cell is destroyed.
            await wait_until(
                lambda: _recorded(hive, "cell.destroyed", cell_id), timeout_s=_TIMEOUT_S
            )
        assert task.status is TaskStatus.SUCCEEDED
        assert len(backend.provision_calls) >= 1
        await _assert_no_false_alarm(hive)
        events = await hive.stores.trail.query(TrailQuery())
        cell_node_id = _cell_node_id(events, cell_id)
        assert cell_node_id != str(hive.manifest.hive.node_id)
        llm_calls = [e for e in events if e.kind == "llm.call" and e.node_id == cell_node_id]
        assert llm_calls, "no llm.call from the Cell's own node reached the Queen's trail"
    finally:
        await backend.aclose()


def test_a_goal_withdrawn_while_its_cell_is_provisioned_leaves_no_cell_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Withdrawn mid-provision: her tick hears the Hive Stand throughout; the Cell is destroyed."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    tuning = VirtualCellsTuning(
        prefer="virtual", overwinter_enabled=False, heartbeat_interval_s=_HEARTBEAT_INTERVAL_S
    )
    manifest_path = virtual_cells_manifest(tmp_path, tuning=tuning)
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_run_withdrawn_mid_provision(hive))


async def _run_withdrawn_mid_provision(hive: Hive) -> None:
    """Withdraw a goal while its Cell is provisioned, then read the chamber, trail and backend."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    backend.set_provision_delay(_PROVISION_DELAY_S)
    stand = hive.warden_link.warden_id
    try:
        async with run_hive(hive):
            goal_id = await hive.queen.submit_goal(
                "write one haiku about bees", clearance=HoneyClearance.C1
            )
            # Stamped as provisioning begins, while the backend is still at work.
            await wait_until(
                lambda: _recorded_kind(hive, "cell.provisioning"), timeout_s=_TIMEOUT_S
            )
            [began] = await hive.stores.trail.query(TrailQuery(kind="cell.provisioning"))
            await wait_until(lambda: _heard_through(hive, stand, began.at), timeout_s=_TIMEOUT_S)
            still_provisioning = not await _recorded_kind(hive, "cell.provisioned")
            assert await hive.queen.cancel_goal(goal_id, "withdrawn")
            await wait_until(
                lambda: _recorded(hive, "cell.destroyed", began.subject_id), timeout_s=_TIMEOUT_S
            )
        assert still_provisioning
        [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        assert task.status is TaskStatus.CANCELLED
        events = await hive.stores.trail.query(TrailQuery())
        assert not [e for e in events if e.kind == "queen.assigned"]
        released = [e for e in events if e.payload.get("reason") == "cell_released"]
        assert [(e.payload["cause"], e.payload["cell_id"]) for e in released] == [
            ("task_gone", began.subject_id)
        ]
        assert await backend.list_cells(hive.manifest.hive.id) == ()
    finally:
        await backend.aclose()


def _heard_through(hive: Hive, warden_id: WardenId, since: datetime) -> bool:
    """True once her tick has handled a Heartbeat from `warden_id` received well past `since`."""
    heard = hive.queen.liveness[warden_id].last_heartbeat_at
    return heard is not None and heard >= since + _HEARD_THROUGH_A_PROVISION


async def _recorded_kind(hive: Hive, kind: str) -> bool:
    """True once the Queen's trail holds any event of `kind`."""
    return bool(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _assert_no_false_alarm(hive: Hive) -> None:
    """Assert the Queen raised no CELL_UNREACHABLE Alarm about the Hive Stand's own Warden."""
    stand_warden_id = str(hive.warden_link.warden_id)
    # What the human would have read: every Alarm line the Queen posted to the chat.
    lines = await hive.stores.chat.read(ChatQuery(limit=MAX_CHAT_PAGE))
    unreachable = [
        line.text
        for line in lines
        if line.kind is ChatKind.ALARM
        and AlarmKind.CELL_UNREACHABLE.value in line.text
        and stand_warden_id in line.text
    ]
    assert unreachable == []
    # And what she escalated: no such Alarm is waiting in her human inbox either.
    raised = [
        alarm
        for alarm in hive.queen.human_inbox.alarms
        if alarm.kind is AlarmKind.CELL_UNREACHABLE and alarm.origin == stand_warden_id
    ]
    assert raised == []


async def _goal_finished(hive: Hive, goal_id: TaskId) -> bool:
    """True once every task under `goal_id` has reached a terminal status."""
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    return bool(tasks) and all(is_terminal(task.status) for task in tasks)


async def _recorded(hive: Hive, kind: str, subject_id: str) -> bool:
    """True once the Queen's trail holds an event of `kind` about `subject_id`."""
    return bool(await hive.stores.trail.query(TrailQuery(kind=kind, subject_id=subject_id)))


def _cell_id_for_task(events: Sequence[PheromoneEvent], task_id: str) -> str:
    """Return the Cell id `queen.assigned` named for `task_id`."""
    assigned = next(e for e in events if e.kind == "queen.assigned" and e.subject_id == task_id)
    cell_id = assigned.payload.get("cell_id")
    assert isinstance(cell_id, str)
    return cell_id


def _cell_node_id(events: Sequence[PheromoneEvent], cell_id: str) -> str:
    """Return the node id the Cell's own Warden recorded under, read off its shipped segment.

    `cell.ready` (the Queen's own row) names the Cell's Warden; that Warden's `warden.stopped` is
    written inside the Cell and reaches this trail only through the shipped segment, under the
    Cell's own freshly minted node id (`hivemind.cli.in_cell.config`).
    """
    ready = next(e for e in events if e.kind == "cell.ready" and e.subject_id == cell_id)
    warden_id = ready.payload["warden_id"]
    stopped = next(e for e in events if e.kind == "warden.stopped" and e.subject_id == warden_id)
    return stopped.node_id
