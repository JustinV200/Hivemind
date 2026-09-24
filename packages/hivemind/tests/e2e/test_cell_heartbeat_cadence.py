"""End-to-end: a Virtual Cell's Warden, slower than the manifest's cadence, is never unreachable.

A Virtual Cell's in-Cell Warden heartbeats every 15 s (`hivemind.cli.in_cell.config`'s own default),
while the Queen used to judge every Warden against the manifest's `[supervision]` window: any
Virtual Cell that lived past 15 s drew a false `CELL_UNREACHABLE` the moment its first Heartbeat
was followed by one manifest window of silence. This module runs a whole Hive -- the Queen, the
Hive Stand's own Warden, `CellListener`, and a real in-Cell Warden over
`builders.virtual_cells.ContainerSpawningFakeCellBackend` -- with Overwintering on, so the Cell (its
pause a no-op on the fake backend) and its Warden outlive their one task. It waits for that
Warden's first Heartbeat, then for the Queen to go on judging it for ten of the manifest's windows
past it (well short of the Cell's own 45 s window), and checks no Alarm was raised about it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.liveness for the interval each Warden is judged by.
    - hivemind.queen.inbox.links for Pulse, the declared cadence each link's reader hears.
    - tests.e2e.test_slow_provision for the same Hive built around a slow provision.
"""

from __future__ import annotations

import asyncio
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

from hivemind.brood_chamber import TaskFilter, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.cli.in_cell.config import DEFAULT_HEARTBEAT_INTERVAL_S
from hivemind.manifest import load_manifest
from hivemind.queen.chat import MAX_CHAT_PAGE, ChatKind, ChatQuery
from hivemind.supervision import AlarmKind
from waggle.clock import SystemClock
from waggle.ids import TaskId, WardenId

pytestmark = pytest.mark.e2e

# The test manifest's own cadence: a 0.3 s window (three misses) that a 15 s Warden overruns fifty
# times over, and still wide enough that scheduling jitter never looks like silence.
_HEARTBEAT_INTERVAL_S = 0.1
_WINDOWS_PAST = 10  # How many manifest windows the Queen must go on judging it past its beat.
_GOAL_TIMEOUT_S = 20.0
# The in-Cell Warden's first Heartbeat comes one of its own intervals after it starts.
_FIRST_BEAT_TIMEOUT_S = DEFAULT_HEARTBEAT_INTERVAL_S + 15.0


def test_a_virtual_cells_warden_is_judged_by_the_cadence_it_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Virtual Cell living ten manifest windows past its first 15 s Heartbeat raises no Alarm."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    tuning = VirtualCellsTuning(
        prefer="virtual", overwinter_enabled=True, heartbeat_interval_s=_HEARTBEAT_INTERVAL_S
    )
    manifest_path = virtual_cells_manifest(tmp_path, tuning=tuning)
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_run_past_the_first_beat(hive))


async def _run_past_the_first_beat(hive: Hive) -> None:
    """Run one goal onto a Virtual Cell, then keep the Hive up well past its Warden's first beat."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    supervision = hive.manifest.supervision
    window = timedelta(seconds=supervision.heartbeat_interval_s * supervision.heartbeat_miss_limit)
    # The premise: the Cell's own cadence is far slower than the manifest's whole window.
    assert timedelta(seconds=DEFAULT_HEARTBEAT_INTERVAL_S) > _WINDOWS_PAST * window
    try:
        async with run_hive(hive):
            goal_id = await hive.queen.submit_goal(
                "write one haiku about bees", clearance=HoneyClearance.C1
            )
            await wait_until(lambda: _goal_finished(hive, goal_id), timeout_s=_GOAL_TIMEOUT_S)
            cell_warden = _virtual_cell_warden(hive)
            await wait_until(
                lambda: _last_beat(hive, cell_warden) is not None, timeout_s=_FIRST_BEAT_TIMEOUT_S
            )
            first_beat = _last_beat(hive, cell_warden)
            assert first_beat is not None
            # She goes on judging it tick after tick: until she stands on a Stand Heartbeat sent
            # ten of the manifest's windows after the Cell's one and only beat so far.
            stand = hive.warden_link.warden_id
            past = first_beat + _WINDOWS_PAST * window
            await wait_until(lambda: _beat_since(hive, stand, past), timeout_s=_GOAL_TIMEOUT_S)
            row = hive.queen.liveness[cell_warden]
        assert row.last_heartbeat_at == first_beat  # Silent all along, by the manifest's clock.
        assert row.interval_s == DEFAULT_HEARTBEAT_INTERVAL_S
        assert not row.is_offline
        assert await _unreachable(hive, cell_warden) == []
    finally:
        await backend.aclose()


def _virtual_cell_warden(hive: Hive) -> WardenId:
    """Return the one attached Warden that is not the Hive Stand's own: the Cell's."""
    stand = hive.warden_link.warden_id
    [cell_warden] = [link.warden_id for link in hive.queen.wardens if link.warden_id != stand]
    return cell_warden


def _last_beat(hive: Hive, warden_id: WardenId) -> datetime | None:
    """When `warden_id`'s newest Heartbeat the Queen has judged by was sent; None before one."""
    row = hive.queen.liveness.get(warden_id)
    return row.last_heartbeat_at if row is not None else None


def _beat_since(hive: Hive, warden_id: WardenId, since: datetime) -> bool:
    """True once the Queen's liveness for `warden_id` stands on a Heartbeat sent after `since`."""
    beat = _last_beat(hive, warden_id)
    return beat is not None and beat > since


async def _unreachable(hive: Hive, warden_id: WardenId) -> list[str]:
    """Every CELL_UNREACHABLE the Queen raised about `warden_id`: chat lines and inbox Alarms."""
    lines = await hive.stores.chat.read(ChatQuery(limit=MAX_CHAT_PAGE))
    posted = [
        line.text
        for line in lines
        if line.kind is ChatKind.ALARM
        and AlarmKind.CELL_UNREACHABLE.value in line.text
        and warden_id in line.text
    ]
    raised = [
        str(alarm.id)
        for alarm in hive.queen.human_inbox.alarms
        if alarm.kind is AlarmKind.CELL_UNREACHABLE and alarm.origin == warden_id
    ]
    return posted + raised


async def _goal_finished(hive: Hive, goal_id: TaskId) -> bool:
    """True once every task under `goal_id` has reached a terminal status."""
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    return bool(tasks) and all(is_terminal(task.status) for task in tasks)
