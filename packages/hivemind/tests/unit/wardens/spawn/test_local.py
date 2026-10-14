"""Tests for hivemind.wardens.spawn.local: starting a sub-bee for a task the Warden minted itself.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/local.py (codingrules section 3). The row it returns is
    marked as the Warden's own and its bee is offered no Comb tools, so it cannot reach the Royal
    Jelly Lab or the Quarantine Comb; and the bee is still bound through the Guard, so a grant that
    does not allow its slot refuses it with nothing started.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.local for the module under test.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for why the Warden asks for itself.
"""

from __future__ import annotations

import dataclasses
from typing import cast

import pytest
from builders.diagnose import hangs, start_nuc_scene
from builders.forage import make_source
from builders.offline_warden import OfflineScene

from hivemind.cell import RequestOrigin
from hivemind.pheromone import TrailQuery
from hivemind.wardens.errors import BindingRefusedError
from hivemind.wardens.offline.diagnose import LocalBinding, LocalPlan, local_assignment
from hivemind.wardens.spawn import LocalAssignment, WardenCellContext, spawn_local, stop_sub_bee
from hivemind.wardens.spawn.comb import CombSeams
from hivemind.wardens.ticks.alarms import cell_context
from hivemind.workers.state import WorkerState


async def _wanted_and_context(
    allowed: bool = True,
) -> tuple[LocalAssignment, WardenCellContext, OfflineScene]:
    """An idle Nuc, a task it minted for itself, and the Warden's context to start it in."""
    scene = await start_nuc_scene(hangs())
    ctx = cell_context(scene.warden)
    assert ctx is not None
    plan = LocalPlan(
        warden_id=scene.warden._warden_id,
        cell=ctx.cell,
        binding=LocalBinding("worker", make_source(source_id="small", provider="fake")),
        peer="hive.example",
        earlier=(),
        valid_for_s=60.0,
        attempt=1,
    )
    wanted = local_assignment(plan, scene.clock)
    if not allowed:
        wanted = dataclasses.replace(wanted, grant=wanted.grant.model_copy(update={"allowed": ()}))
    return wanted, ctx, scene


async def test_a_local_sub_bee_is_the_wardens_own_and_is_offered_no_comb_tools() -> None:
    wanted, ctx, scene = await _wanted_and_context()
    # A Comb the Warden holds: were it passed on, building the bee's tools from it would fail.
    greedy = dataclasses.replace(ctx, comb=cast(CombSeams, object()))

    sub_bee = await spawn_local(greedy, wanted)

    assert sub_bee.local is not None and sub_bee.local.origin is RequestOrigin.WARDEN
    assert sub_bee.local.claim is None and sub_bee.local.alarm is None
    assert sub_bee.comb_tools is None
    assert sub_bee.task_id == wanted.assignment.task_id
    assert sub_bee.binding == "worker" and sub_bee.state is WorkerState.SPAWNED
    assert sub_bee.worker_id not in scene.warden._sub_bees  # Registering the row is the caller's.
    spawned = await scene.deps.trail.query(TrailQuery(kind="worker.spawned"))
    assert [e.payload["task_id"] for e in spawned] == [wanted.assignment.task_id]
    await stop_sub_bee(sub_bee, scene.clock)
    await scene.stop()


async def test_a_grant_that_forbids_the_slot_refuses_the_binding_and_starts_nothing() -> None:
    wanted, ctx, scene = await _wanted_and_context(allowed=False)

    with pytest.raises(BindingRefusedError):
        await spawn_local(ctx, wanted)

    assert not await scene.deps.trail.query(TrailQuery(kind="worker.spawned"))
    denied = await scene.deps.trail.query(TrailQuery(kind="guard.denied"))
    assert len(denied) == 1  # The Guard recorded the refusal, as for any sub-bee.
    await scene.stop()
