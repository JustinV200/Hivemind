"""Tests for hivemind.queen.swarm_gate.rungs.readings: what the Queen reads off the Hive per pass.

The Queen's own rung decisions weigh the Hive, not one device: how loaded the Hive Stand is by the
sub-bees of the Level 0 Wardens, how strained the shared seats are, and which devices have a task
that must survive a lost link. These tests build a Hive in the Queen's own stores (tasks in the
Brood Chamber, the Hive Stand's capacity and the seats in the Forage ledger) and read it once, to
see that each figure counts what its definition says and nothing else, and that reading changes
nothing.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/readings.py (codingrules section 3). The Queen,
    her stores and the registry are the real classes over memory.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.readings for the module under test.
    - hivemind.queen.swarm_gate.rungs.pressure for the figures it hands the numbers to.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity, make_grant
from builders.swarm import GateRig, report_stand, run_on, survival_task

from hivemind.brood_chamber import TaskStatus
from hivemind.forage import GrantState, SeatReservation
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import GateContext, open_tasks
from hivemind.queen.swarm_gate.rungs.pressure import BeeLoad
from hivemind.queen.swarm_gate.rungs.readings import Readings, read_world
from hivemind.swarm import RuntimeLevel, SwarmNode

_MIB = 1024**2
_GIB = 1024**3
_BEE = BeeLoad(1.0, 512 * _MIB)  # What one Drone costs: the Queen's default footprint.


async def _read(rig: GateRig, *nodes: SwarmNode) -> Readings:
    """Read the Hive as the Queen does at the start of a pass."""
    return await read_world(GateContext(rig.queen, rig.queen_deps), nodes)


async def test_a_hive_with_nothing_in_it_reads_as_quiet(rig: GateRig) -> None:
    readings = await _read(rig)

    assert readings.survival == frozenset() and readings.busy == frozenset()
    assert readings.stand == BeeLoad() and readings.by_cell == {}
    assert readings.host is None and readings.seats == 0.0


async def test_the_hive_stands_capacity_is_the_one_its_own_warden_reported(rig: GateRig) -> None:
    await report_stand(rig, cores=12, memory_bytes=32 * _GIB)

    readings = await _read(rig)

    assert readings.host is not None
    assert readings.host.cores == 12 and readings.host.memory_bytes == 32 * _GIB


async def test_the_capacity_a_device_reported_is_not_the_hive_stands(rig: GateRig) -> None:
    device = await rig.enrol()
    await rig.queen_deps.ledger.report_capacity(device.cell_id, make_capacity())

    assert (await _read(rig, device)).host is None  # So no relief is ever judged off its size.


async def test_the_running_sub_bees_of_level_0_devices_load_the_hive_stand(rig: GateRig) -> None:
    one, two = await rig.enrol(), await rig.enrol()
    await run_on(rig, one, count=2)
    await run_on(rig, two)

    readings = await _read(rig, one, two)

    assert readings.stand == BeeLoad(3.0, 3 * 512 * _MIB)
    assert readings.by_cell[one.cell_id] == BeeLoad(2.0, 2 * 512 * _MIB)
    assert readings.by_cell[two.cell_id] == _BEE


async def test_the_running_sub_bees_of_a_colonized_device_load_it_and_not_the_hive_stand(
    rig: GateRig,
) -> None:
    away = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await run_on(rig, away, count=2)

    readings = await _read(rig, away)

    assert readings.stand == BeeLoad()
    assert readings.by_cell[away.cell_id] == BeeLoad(2.0, 2 * 512 * _MIB)


async def test_a_task_that_is_assigned_but_not_running_is_busy_and_loads_nothing(
    rig: GateRig,
) -> None:
    device = await rig.enrol()
    await survival_task(
        rig.queen_deps,
        survives=False,
        on=(device.warden_id, device.cell_id),
        status=TaskStatus.ASSIGNED,
    )

    readings = await _read(rig, device)

    assert readings.busy == {device.cell_id}
    assert readings.stand == BeeLoad() and readings.by_cell == {}


async def test_a_task_that_has_ended_is_not_read_at_all(rig: GateRig) -> None:
    device = await rig.enrol()
    task = await survival_task(
        rig.queen_deps,
        survives=True,
        on=(device.warden_id, device.cell_id),
        status=TaskStatus.RUNNING,
    )
    await rig.queen_deps.chamber.cancel(task.id, "The operator cancelled the goal.")

    readings = await _read(rig, device)

    assert readings.busy == frozenset() and readings.survival == frozenset()
    assert readings.stand == BeeLoad()


async def test_a_task_that_waits_for_a_device_it_is_pinned_to_asks_survival_of_it(
    rig: GateRig,
) -> None:
    device = await rig.enrol()
    await survival_task(rig.queen_deps, pinned_to=device.cell_id)

    readings = await _read(rig, device)

    assert readings.survival == {device.cell_id}
    assert readings.busy == frozenset()  # Nothing is on it yet.


async def test_a_task_placed_on_a_device_that_does_not_set_the_flag_asks_nothing(
    rig: GateRig,
) -> None:
    device = await rig.enrol()
    await run_on(rig, device)

    assert (await _read(rig, device)).survival == frozenset()


async def test_reading_the_hive_changes_nothing_in_it(rig: GateRig) -> None:
    device = await rig.enrol()
    await run_on(rig, device)
    await report_stand(rig)
    before = rig.queen_deps.ledger.headroom()
    tasks = await open_tasks(rig.queen_deps)
    events = await rig.queen_deps.trail.query(TrailQuery())

    await _read(rig, device)
    await _read(rig, device)

    assert rig.queen_deps.ledger.headroom() == before
    assert await open_tasks(rig.queen_deps) == tasks
    assert await rig.queen_deps.trail.query(TrailQuery()) == events


async def _seats(rig: GateRig, *, total: int, held: int) -> None:
    """Declare `total` shared seats on one source and have a live grant hold `held` of them."""
    ledger = rig.queen_deps.ledger
    await ledger.seats.set_capacity("box", total)
    if held:
        grant = make_grant(
            GrantState.ACTIVE, rig.clock, seats=(SeatReservation(source_id="box", seats=held),)
        )
        await ledger.record_grant(grant)


async def test_the_seat_pressure_is_the_share_of_the_usable_seats_the_grants_hold(
    rig: GateRig,
) -> None:
    await _seats(rig, total=11, held=4)
    usable = 11 - rig.queen_deps.ledger.reserve.seats  # The reserve is not the pool's to hand out.

    readings = await _read(rig)

    assert readings.seats == pytest.approx(4 / usable)


async def test_the_pools_seat_pressure_bears_only_on_a_device_with_a_bee_running(
    rig: GateRig,
) -> None:
    await _seats(rig, total=11, held=5)
    busy, idle = await rig.enrol(level=RuntimeLevel.COLONIZED), await rig.enrol()
    await run_on(rig, busy)

    readings = await _read(rig, busy, idle)

    assert readings.seats_for(busy) == readings.seats == pytest.approx(0.5)
    assert readings.seats_for(idle) == 0.0  # A model server there would take no call off the pool.


async def test_no_seats_held_is_no_pressure_and_none_free_is_all_of_it(rig: GateRig) -> None:
    await _seats(rig, total=9, held=0)
    assert (await _read(rig)).seats == 0.0

    await _seats(rig, total=9, held=9)

    assert (await _read(rig)).seats == 1.0


async def test_a_hive_that_declared_no_seats_has_no_pressure_on_them(rig: GateRig) -> None:
    assert (await _read(rig)).seats == 0.0


async def test_the_share_of_the_hive_stand_a_level_0_device_takes_is_its_own_bees(
    rig: GateRig,
) -> None:
    await report_stand(rig, cores=8, memory_bytes=16 * _GIB)
    heavy, light = await rig.enrol(), await rig.enrol()
    await run_on(rig, heavy, count=4)
    await run_on(rig, light)

    readings = await _read(rig, heavy, light)

    assert readings.share_of(heavy) == pytest.approx(0.5)  # Four of eight cores.
    assert readings.share_of(light) == pytest.approx(0.125)


async def test_a_device_off_the_hive_stand_takes_no_share_of_it(rig: GateRig) -> None:
    await report_stand(rig)
    away = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await run_on(rig, away, count=2)

    assert (await _read(rig, away)).share_of(away) == 0.0


async def test_a_hive_stand_that_reported_nothing_is_taken_no_share_of(rig: GateRig) -> None:
    device = await rig.enrol()
    await run_on(rig, device, count=3)

    readings = await _read(rig, device)

    assert readings.share_of(device) == 0.0 and readings.relief_for(device) == 0.0


async def test_a_device_with_no_bee_running_bears_no_relief_however_full_the_hive_stand_is(
    rig: GateRig,
) -> None:
    await report_stand(rig, cores=2)
    busy, idle = await rig.enrol(), await rig.enrol()
    away = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await run_on(rig, busy, count=2)  # The Hive Stand is full, and not by the others' doing.

    readings = await _read(rig, busy, idle, away)

    assert readings.relief_for(busy) == pytest.approx(1.0)
    assert readings.relief_for(idle) == 0.0  # Moving it takes nothing off the Hive Stand.
    assert readings.relief_for(away) == 0.0  # And bringing it back would put nothing on it.


async def test_the_relief_a_level_0_device_bears_on_is_the_whole_stands_load(rig: GateRig) -> None:
    await report_stand(rig, cores=8, memory_bytes=16 * _GIB)
    one, two = await rig.enrol(), await rig.enrol()
    await run_on(rig, one, count=3)
    await run_on(rig, two, count=3)

    readings = await _read(rig, one, two)

    assert readings.relief_for(one) == pytest.approx(0.75)  # Six of eight cores, whoever's.
    assert readings.relief_for(two) == pytest.approx(0.75)


async def test_the_relief_a_colonized_device_bears_on_counts_its_own_bees_back_in(
    rig: GateRig,
) -> None:
    await report_stand(rig, cores=8, memory_bytes=16 * _GIB)
    staying = await rig.enrol()
    away = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await run_on(rig, staying, count=2)
    await run_on(rig, away, count=3)

    readings = await _read(rig, staying, away)

    assert readings.relief_for(staying) == pytest.approx(0.25)  # Its own and the others': two.
    assert readings.relief_for(away) == pytest.approx(0.625)  # If it came back: five of eight.
