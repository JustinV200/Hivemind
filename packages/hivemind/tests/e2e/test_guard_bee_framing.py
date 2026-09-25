"""End-to-end: a Virtual Cell frames another Cell's bee; the framer is isolated, the framed never.

Roadmap step 10.6's attribution on a real run. The Hive is composed by `build_hive` with its
Virtual side over the container-spawning fake backend: a real `CellListener` and, per Cell, a real
in-Cell Warden whose Drone starts a long command and keeps at it. A two-task goal lands on two
Virtual Cells. The Queen records each Warden's attach with the node its handshake proved, places
each task on its Cell, and each Cell's Warden ships its own trail segment.

Then one Cell turns hostile: its own trail, the one its Warden ships, gets the injection
correlation (a flag, then a denial) naming the other Cell's bee and task, the exact dire pattern
that isolates a Cell by rule. The segment merges (its own node, so the Cell gate has nothing to
refuse), and the composed Guard Bee reads it. It never counts the records as what they say: they
were recorded on a node that speaks for another Cell, so it reports the recording node's Cell for
forging (`subject_forgery`, CRITICAL, a shipped dire pattern), and the Queen isolates the framer
by rule. The framed Cell is never isolated, and no report ever names its bee or task.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.workers.roles.guard_bee.test_attribution for the same over one Guard Bee round.
    - tests.e2e.test_guard_bee_on_virtual_cell for a Cell isolated on its own lured Drone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.guard_bee import LingeringCells, TrailSeeder, quick_rounds, ship_on_every_call
from builders.virtual_cells import (
    independent_haiku_plan,
    patch_submit_goal_dispatch_race,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.wardens.deps import WardenDeps
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_WAIT_S = 30.0  # Each wait; two Cells boot, work and ship within a few seconds on loopback.
_SETTLE_S = 3.0  # Six more Guard Bee rounds after the framer's isolation: time to act again.
_TWO_TASKS = independent_haiku_plan(("haiku_1.txt", "haiku_2.txt"))
# A goal ceiling without `cell:hive_stand`: every task of it lands on a Virtual Cell.
_VIRTUAL_ONLY = (
    "cell:virtual",
    "cell:comb_shield:*",
    "llm:*",
    "tool:*",
    "fs:read:**",
    "fs:write:**",
    "exec:*",
    "question:human",
)


@dataclass(frozen=True, slots=True)
class _Working:
    """One Virtual Cell at work: its id, node, bee and task, and its in-Cell Warden's deps."""

    cell: str
    node: str
    bee: str
    task: str
    deps: WardenDeps


def _hive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Hive, list[WardenDeps]]:
    """Compose the Hive with two lingering Virtual Cells' worth of goal, as `hive run` does."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", LingeringCells
    )
    patch_submit_goal_dispatch_race(monkeypatch)
    built = ship_on_every_call(monkeypatch)  # Each Cell's spawn reaches the Queen at once.
    script = HaikuScript(default_worker_turn, plan=_TWO_TASKS)
    hive = build_hive(
        load_manifest(quick_rounds(virtual_cells_manifest(tmp_path)), {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    return hive, built


async def _events(hive: Hive, kind: str) -> list[PheromoneEvent]:
    """Every `kind` event on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _working(hive: Hive, built: list[WardenDeps]) -> list[_Working]:
    """Every Virtual Cell whose bee's spawn has reached the Queen, as the Queen recorded it."""
    cells = {
        e.payload.get("node_id"): e.payload["cell_id"]
        for e in await _events(hive, "warden.spawned")
    }
    by_node = {deps.identity.node_id: deps for deps in built}
    found = []
    for spawn in await _events(hive, "worker.spawned"):
        node, deps = spawn.node_id, by_node.get(spawn.node_id)
        if node in cells and deps is not None:
            task = str(spawn.payload["task_id"])
            found.append(_Working(str(cells[node]), node, spawn.subject_id, task, deps))
    return found


async def _frame(framer: _Working, victim: _Working) -> None:
    """The framer's own trail names the victim's bee and task, then its Warden ships it."""
    identity = CellIdentity(
        hive_id=framer.deps.identity.hive_id, node_id=framer.deps.identity.node_id, actor="system"
    )
    seed = TrailSeeder(framer.deps.trail, SystemClock(), identity)
    await seed.injection(victim.bee, victim.task)
    await seed.denied(victim.bee)
    assert framer.deps.trail_sync is not None
    await framer.deps.trail_sync.sync()


async def _isolated(hive: Hive) -> set[str]:
    """Every Cell the Queen has isolated so far."""
    return {event.subject_id for event in await _events(hive, "cell.isolated")}


async def _scenario(hive: Hive, built: list[WardenDeps]) -> tuple[_Working, _Working]:
    """Frame one working Cell's bee from the other; wait for the Queen, then a while longer."""
    backend = hive.virtual_cells.registry.get("fake") if hive.virtual_cells else None
    assert isinstance(backend, LingeringCells)
    try:
        async with run_hive(hive):
            await hive.queen.submit_goal(
                "write two haiku", clearance=HoneyClearance.C1, capabilities=_VIRTUAL_ONLY
            )
            await wait_until(lambda: _two_working(hive, built), timeout_s=_WAIT_S)
            victim, framer = (await _working(hive, built))[:2]
            await _frame(framer, victim)
            await wait_until(lambda: _has_isolated(hive), timeout_s=_WAIT_S)
            await asyncio.sleep(_SETTLE_S)  # Rounds enough for any second request to land.
            await _assert_only_the_framer_isolated(hive, framer, victim)
    finally:
        await backend.aclose()
    return victim, framer


async def _two_working(hive: Hive, built: list[WardenDeps]) -> bool:
    """Whether two Cells' bees have reached the Queen, each from its own attached node."""
    return len({working.cell for working in await _working(hive, built)}) >= 2


async def _has_isolated(hive: Hive) -> bool:
    """Whether the Queen has isolated any Cell yet."""
    return bool(await _isolated(hive))


async def _assert_only_the_framer_isolated(hive: Hive, framer: _Working, victim: _Working) -> None:
    """The framer isolated by rule on its own report; the victim never, and never named."""
    assert await _isolated(hive) == {framer.cell}
    filed = [a for a in await _events(hive, "guard.alert") if a.payload["disposition"] == "filed"]
    assert [(a.payload["rule"], a.payload["cell_id"]) for a in filed] == [
        ("subject_forgery", framer.cell)
    ]
    for alert in await _events(hive, "guard.alert"):
        named = [alert.payload.get("bee_ids"), alert.payload.get("task_ids")]
        ids = {str(value) for listed in named if isinstance(listed, list) for value in listed}
        assert not ids & {victim.bee, victim.task}  # No report ever names what was framed.
    [decided] = await _events(hive, "queen.decided")
    assert (decided.payload["action"], decided.payload["basis"]) == ("ISOLATE_CELL", "rule")
    assert decided.payload["rule"] == "subject_forgery"


def test_a_cell_framing_another_cells_bee_is_isolated_and_the_framed_cell_never(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive, built = _hive(tmp_path, monkeypatch)

    victim, framer = asyncio.run(_scenario(hive, built))

    assert victim.cell != framer.cell
