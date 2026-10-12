"""Tests for hivemind.wardens.spawn.comb and spawn's Comb wiring: a sub-bee's three Comb fields.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/comb.py (codingrules section 3). The attenuation is tested
    pure; the wiring is tested through `spawn_sub_bee` on a started Warden, with a worker that
    captures the `WorkerContext` it was given.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.comb for the module under test.
    - hivemind.wardens.ticks.tools.seams for what a Warden offers spawn.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable

from builders.comb_tools import make_comb_package
from builders.royal_jelly import make_offer, make_package, make_tool_spec
from builders.wardens import make_warden_deps
from builders.wardens_tools import ToolRig, make_resident, make_scaffolder_grant
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.guard import CapabilitySet
from hivemind.royal_jelly import QueenPrincipal
from hivemind.wardens.spawn import (
    CombSeams,
    WardenCellContext,
    make_handles,
    spawn_sub_bee,
    stop_sub_bee,
    visible_entries,
)
from hivemind.wardens.ticks.basket import WardenRetainSink
from hivemind.wardens.ticks.tools import comb_seams
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_task_id, new_worker_id
from waggle.messages.task import TaskAssign, WorkerRole
from waggle.messages.tool.authoring import ToolScope

_WIDE = CapabilitySet.parse("tool:*", "net:example.org")


def _capturing(captured: dict[str, WorkerContext]) -> Callable[[WorkerRole], ScriptedWorker]:
    """Build a worker_factory whose worker records the context it was started with."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        captured["ctx"] = ctx
        await asyncio.sleep(0)
        return make_outcome()

    return lambda role: ScriptedWorker(script, role=role)


async def _captured(captured: dict[str, WorkerContext]) -> WorkerContext:
    """Wait (a few turns) until the spawned worker has recorded its context; return it."""
    for _ in range(20):
        if "ctx" in captured:
            break
        await asyncio.sleep(0)
    return captured["ctx"]


async def _spawn(rig: ToolRig, comb: CombSeams | None) -> tuple[WorkerContext, object]:
    """Spawn one sub-bee on the rig's Warden with `comb` and return its context and its row."""
    captured: dict[str, WorkerContext] = {}
    warden = rig.warden
    assert warden._cell and warden._lease and warden._session
    deps = dataclasses.replace(warden._deps, worker_factory=_capturing(captured))
    ctx = WardenCellContext(
        warden_id=warden._warden_id,
        deps=deps,
        ceiling=warden._ceiling,
        cell=warden._cell,
        lease=warden._lease,
        session=warden._session,
        comb=comb,
    )
    assignment = make_assignment(clock=rig.clock, cell_id=warden._cell.id)
    grant = make_scaffolder_grant(warden, rig.clock)
    sub_bee = await spawn_sub_bee(ctx, assignment, grant)
    worker_ctx = await _captured(captured)
    await stop_sub_bee(sub_bee, rig.clock)
    await sub_bee.link.close()
    return worker_ctx, sub_bee


def test_a_cell_tool_is_offered_only_to_the_bees_of_its_cell() -> None:
    here, elsewhere = new_cell_id(FakeClock()), new_cell_id(FakeClock())
    ours = make_comb_package(name="ours", scope=ToolScope.CELL, cell_id=here).entry
    theirs = make_comb_package(name="theirs", scope=ToolScope.CELL, cell_id=elsewhere).entry
    shared = make_comb_package(name="shared").entry

    assert visible_entries((ours, theirs, shared), _WIDE, here) == (ours, shared)


def test_a_tool_is_offered_only_when_the_bee_holds_it_and_what_it_needs() -> None:
    here = new_cell_id(FakeClock())
    needs_net = make_comb_package(name="fetch", required_capabilities=("net:example.org",)).entry
    plain = make_comb_package(name="plain").entry
    unreadable = make_comb_package(name="odd", required_capabilities=("net:example.org",)).entry
    unreadable = dataclasses.replace(unreadable, required_capabilities=("nonsense",))

    no_net = CapabilitySet.parse("tool:*")
    only_plain = CapabilitySet.parse("tool:plain")

    assert visible_entries((needs_net, plain, unreadable), _WIDE, here) == (needs_net, plain)
    assert visible_entries((needs_net, plain, unreadable), no_net, here) == (plain,)
    assert visible_entries((needs_net, plain, unreadable), only_plain, here) == (plain,)
    assert visible_entries((plain,), CapabilitySet.empty(), here) == ()


async def test_no_seams_means_no_handles() -> None:
    clock = FakeClock()

    assert (
        await make_handles(
            None, _WIDE, new_cell_id(clock), (new_worker_id(clock), new_task_id(clock))
        )
        is None
    )


async def test_spawn_fills_the_bundle_the_source_and_a_retain_sink_for_that_worker(
    hive_rig: ToolRig,
) -> None:
    spec = make_tool_spec(required_capabilities=())
    report, offer = make_offer(spec)
    package = make_package(spec=spec)
    await make_resident(hive_rig, package)
    assert hive_rig.access is not None
    await hive_rig.access.registry.promote(spec, report, offer, QueenPrincipal(), "Passed.")

    worker_ctx, sub_bee = await _spawn(hive_rig, comb_seams(hive_rig.warden))

    assert worker_ctx.comb_tools is not None
    assert [entry.name for entry in worker_ctx.comb_tools.entries()] == [spec.name]
    assert worker_ctx.comb_tools is sub_bee.comb_tools  # type: ignore[attr-defined]
    assert worker_ctx.package_source is not None
    assert await worker_ctx.package_source.bytes_for(offer.package_sha256) == package
    assert isinstance(worker_ctx.retain_sink, WardenRetainSink)


async def test_a_warden_with_no_basket_offers_its_bees_no_comb_tools() -> None:
    deps, _queen, warden_id = make_warden_deps()

    assert comb_seams(Warden(warden_id, deps)) is None
