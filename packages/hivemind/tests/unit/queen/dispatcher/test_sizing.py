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
from builders.tasks import make_task

from hivemind.forage import ForageCapacity, GrantBound
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher.sizing import size_grant

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


async def test_size_grant_records_nothing() -> None:
    deps, link, _end = make_queen_deps()

    await size_grant(deps, dataclasses.replace(link, live_capacity=_busy_reading), make_task())

    assert await deps.trail.query(TrailQuery()) == ()
