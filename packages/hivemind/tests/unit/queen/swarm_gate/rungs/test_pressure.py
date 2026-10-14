"""Tests for hivemind.queen.swarm_gate.rungs.pressure: the two figures the Queen's decisions weigh.

The relief pressure is the share of the Hive Stand's cores or memory that the sub-bees of Level 0
Wardens take, whichever is larger; the seat pressure is the share of the shared pool's usable seats
that live grants hold. Both are pure, so each is a table: what is counted, what is not, the edges
(a host of nothing, a pool of nothing, a figure past 1) and the way a device's own bees are added
back for the question "would the Hive Stand need relief if this device were taken back".

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/pressure.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.pressure for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_footprint, make_host_capacity

from hivemind.queen.swarm_gate.rungs.pressure import (
    BeeLoad,
    bees_load,
    relief_pressure,
    seat_pressure,
)
from waggle.messages.task import WorkerRole

_GIB = 1024**3


@pytest.mark.parametrize(
    ("cores", "memory_gib", "expected"),
    [
        (0.0, 0, 0.0),  # No bees, no pressure.
        (4.0, 0, 0.5),  # Half the cores.
        (0.0, 8, 0.5),  # Half the memory.
        (2.0, 12, 0.75),  # The larger of the two shares: memory here.
        (6.0, 4, 0.75),  # And cores here.
        (8.0, 16, 1.0),  # Exactly all of it.
        (20.0, 40, 1.0),  # More than all of it is still 1.
    ],
)
def test_relief_pressure_is_the_larger_of_the_cores_share_and_the_memory_share(
    cores: float, memory_gib: int, expected: float
) -> None:
    host = make_host_capacity(cores=8, memory_bytes=16 * _GIB, memory_free_bytes=8 * _GIB)

    assert relief_pressure(BeeLoad(cores, memory_gib * _GIB), host) == pytest.approx(expected)


def test_a_host_that_reports_no_memory_is_pressed_by_its_cores_alone() -> None:
    host = make_host_capacity(cores=8, memory_bytes=0, memory_free_bytes=0)

    assert relief_pressure(BeeLoad(2.0, 8 * _GIB), host) == pytest.approx(0.25)


def test_the_same_bees_press_a_smaller_host_harder() -> None:
    load = BeeLoad(2.0, 2 * _GIB)
    big = make_host_capacity(cores=16, memory_bytes=64 * _GIB, memory_free_bytes=32 * _GIB)
    small = make_host_capacity(cores=4, memory_bytes=8 * _GIB, memory_free_bytes=4 * _GIB)

    assert relief_pressure(load, small) > relief_pressure(load, big)


def test_a_devices_own_bees_added_back_can_put_the_hive_stand_over_the_line() -> None:
    host = make_host_capacity(cores=8, memory_bytes=16 * _GIB, memory_free_bytes=8 * _GIB)
    away = BeeLoad(4.0, 2 * _GIB)  # What the Level 0 Wardens that remain take.
    device = BeeLoad(3.0, 2 * _GIB)  # What the colonized device's bees would take back.

    assert relief_pressure(away, host) == pytest.approx(0.5)
    assert relief_pressure(away.plus(device), host) == pytest.approx(0.875)


def test_loads_add_up_field_by_field() -> None:
    assert BeeLoad(1.0, 10).plus(BeeLoad(0.5, 5)) == BeeLoad(1.5, 15)
    assert BeeLoad().plus(BeeLoad()) == BeeLoad(0.0, 0)


def test_the_load_of_some_bees_is_the_sum_of_their_roles_footprints() -> None:
    footprints = {
        WorkerRole.DRONE: make_footprint(cpu_cores=1.0, memory_bytes=_GIB),
        WorkerRole.FORAGER: make_footprint(cpu_cores=0.5, memory_bytes=2 * _GIB),
    }

    load = bees_load(footprints, [WorkerRole.DRONE, WorkerRole.DRONE, WorkerRole.FORAGER])

    assert load == BeeLoad(2.5, 4 * _GIB)


def test_a_role_with_no_footprint_costs_nothing_here_and_no_bees_cost_nothing() -> None:
    footprints = {WorkerRole.DRONE: make_footprint(cpu_cores=1.0, memory_bytes=_GIB)}

    assert bees_load(footprints, [WorkerRole.FORAGER]) == BeeLoad()
    assert bees_load(footprints, []) == BeeLoad()


@pytest.mark.parametrize(
    ("free", "total", "reserve", "expected"),
    [
        (10, 10, 0, 0.0),  # Nothing held.
        (5, 10, 0, 0.5),  # Half held.
        (0, 10, 0, 1.0),  # No headroom left: the pool is under the pressure hosting plans see.
        (5, 12, 2, 0.5),  # The reserve is not the pool's to hand out: 10 usable, 5 free.
        (0, 12, 2, 1.0),
        (3, 4, 4, 0.0),  # Every seat is the reserve's: nothing to relieve.
        (3, 4, 9, 0.0),  # A reserve larger than the pool.
        (3, 0, 0, 0.0),  # No shared source declared any seats.
        (20, 10, 0, 0.0),  # More free than usable cannot make the pressure negative.
    ],
)
def test_seat_pressure_is_the_share_of_the_usable_seats_the_grants_hold(
    free: int, total: int, reserve: int, expected: float
) -> None:
    assert seat_pressure(
        free_seats=free, total_seats=total, reserve_seats=reserve
    ) == pytest.approx(expected)
