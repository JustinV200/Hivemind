"""Tests for hivemind.queen.dispatcher.sizing: size_grant reads the Cell as it stands, or as probed.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/sizing.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.sizing for the module under test.
    - hivemind.queen.deps for WardenLink.live_capacity, the reader under test.
"""

from __future__ import annotations

import dataclasses

from builders.forage import make_capacity, make_host_capacity
from builders.queen import make_queen_deps
from builders.tasks import make_task, make_task_spec

from hivemind.forage import ForageCapacity, GrantBound, RoleFootprint
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher.sizing import _grant_inputs, size_grant
from waggle.messages.task import WorkerRole

# 8 cores (builders.forage's own host) at a load of 7.9: 0.1 free, under one 1-core Drone.
_BUSY = make_capacity(host=make_host_capacity(cpu_load=7.9 / 8))


async def _busy_reading() -> ForageCapacity:
    """Stand in for the Hive Stand's live reader: the host is busy right now."""
    return _BUSY


async def test_size_grant_reads_the_links_live_capacity_when_it_has_a_reader() -> None:
    deps, link, _end = make_queen_deps()
    live_link = dataclasses.replace(link, live_capacity=_busy_reading)

    sized = await size_grant(deps, live_link, make_task())

    # The reading, not the Cell probed when the link was built, sized this grant.
    assert sized.is_live is True
    assert sized.inputs.cell_capacity == _BUSY
    assert sized.grant.max_sub_bees == 0
    assert sized.limits.max_sub_bees == 0
    assert sized.limits.limited_by is GrantBound.FREE_CORES
    assert sized.waited_s is None


async def test_size_grant_falls_back_to_the_links_own_cell_without_a_reader() -> None:
    deps, link, _end = make_queen_deps()

    sized = await size_grant(deps, link, make_task())

    assert sized.is_live is False
    assert sized.inputs.cell_capacity == link.cell.capacity
    assert sized.grant.max_sub_bees == sized.limits.max_sub_bees >= 1


async def test_size_grant_sizes_from_the_cell_as_probed_when_told_not_to_read_live() -> None:
    # A retry or a resume, which cannot wait, never reads the live figures (sizing's docstring).
    deps, link, _end = make_queen_deps()
    live_link = dataclasses.replace(link, live_capacity=_busy_reading)

    sized = await size_grant(deps, live_link, make_task(), read_live=False)

    assert sized.is_live is False
    assert sized.inputs.cell_capacity == link.cell.capacity
    assert sized.grant.max_sub_bees >= 1


async def test_size_grant_records_nothing() -> None:
    deps, link, _end = make_queen_deps()

    await size_grant(deps, dataclasses.replace(link, live_capacity=_busy_reading), make_task())

    assert await deps.trail.query(TrailQuery()) == ()


def _footprint(memory_bytes: int) -> RoleFootprint:
    """A one-core footprint told apart from another by its memory alone."""
    return RoleFootprint(
        cpu_cores=1.0, memory_bytes=memory_bytes, seats=1, token_rate_per_minute=1_000.0
    )


# ──────────────────────────────────────────────────────────────────────────────
# _grant_inputs: the role's own footprint, falling back to DRONE's
# ──────────────────────────────────────────────────────────────────────────────


async def test_grant_inputs_uses_the_role_s_own_footprint_when_the_manifest_set_one() -> None:
    drone_fp, forager_fp = _footprint(1), _footprint(2)
    deps, link, warden_end = make_queen_deps(
        footprints={WorkerRole.DRONE: drone_fp, WorkerRole.FORAGER: forager_fp}
    )
    task = make_task(spec=make_task_spec(role=WorkerRole.FORAGER))

    inputs = _grant_inputs(deps, link.cell.capacity, task, (link.warden_id, link.cell.id))

    assert inputs.role is WorkerRole.FORAGER
    assert inputs.footprint == forager_fp
    await warden_end.close()


async def test_grant_inputs_falls_back_to_the_drone_footprint_for_an_unbound_role() -> None:
    # forager/scout are never required manifest keys: deps.footprints here only ever has DRONE.
    deps, link, warden_end = make_queen_deps()
    task = make_task(spec=make_task_spec(role=WorkerRole.SCOUT))

    inputs = _grant_inputs(deps, link.cell.capacity, task, (link.warden_id, link.cell.id))

    assert (
        inputs.role is WorkerRole.SCOUT
    )  # The real role is still recorded, only sizing falls back.
    assert inputs.footprint == deps.footprints[WorkerRole.DRONE]
    await warden_end.close()
