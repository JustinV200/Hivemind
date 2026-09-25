"""Tests for the Guard Bee reading the Night Veil boundary: each living Cell's segment, then none.

With a Virtual side the Queen records through the boundary's `VeiledTrail`, whose reads see the
durable trail alone, while a living Night Veil Cell's records (its Warden's shipped segments, the
Queen's own records about it) are held only in that Cell's ephemeral segment. Here a Night Veil
Cell is opened and attached, its task placed, and its Warden's segment (a bee lured into the
injection correlation) merged into its segment, exactly as the Queen's side does. The Guard Bee
reads the segment beside the durable trail and files its request through the Queen's door; its
alert, recorded through the veiled trail, stays in the segment and never reaches the durable
trail. Once the segment is taken at teardown, every fact read from it is forgotten.

Fits into the Hive:
    Mirrors the Night Veil half of src/hivemind/workers/roles/guard_bee/watch.py (codingrules
    section 3), split by feature from test_watch.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.test_guard_bee_on_night_veil for the same through a whole Hive.
"""

from __future__ import annotations

from dataclasses import dataclass

from builders.cells import make_identity
from builders.guard_bee import (
    GuardBeeRig,
    RigOptions,
    TrailSeeder,
    bind_cell,
    cell_episode,
    make_guard_bee,
)

from hivemind.pheromone import (
    EphemeralSegments,
    MemoryPheromoneTrail,
    TrailQuery,
    VeiledTrail,
)
from hivemind.workers.roles.guard_bee import TrailWatch, load_guard_rules
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_node_id

_ALERTS = TrailQuery(kind="guard.alert")
_HORIZON_S = 3_600.0  # The own-alert horizon the bare watch here is built with.


@dataclass(frozen=True, slots=True)
class _Veiled:
    """A Guard Bee over the Night Veil boundary, and the boundary's two halves."""

    rig: GuardBeeRig
    durable: MemoryPheromoneTrail
    segments: EphemeralSegments


def _veiled() -> _Veiled:
    """A Guard Bee recording through a `VeiledTrail`, as a Hive with a Virtual side builds it."""
    clock = FakeClock()
    durable, segments = MemoryPheromoneTrail(clock), EphemeralSegments(clock)
    rig = make_guard_bee(RigOptions(clock=clock, trail=VeiledTrail(durable, segments)))
    return _Veiled(rig=rig, durable=durable, segments=segments)


async def _lured_night_veil_cell(veiled: _Veiled) -> CellId:
    """A living Night Veil Cell whose shipped segment holds a bee's injection correlation."""
    rig, cell = veiled.rig, new_cell_id(veiled.rig.clock)
    veiled.segments.open(cell)
    node = new_node_id(rig.clock)
    await bind_cell(rig.seed, node, cell)  # The Queen's records about it: kept in its segment.
    local = MemoryPheromoneTrail(rig.clock)  # The Cell's own trail, inside the Cell.
    identity = make_identity(rig.clock, hive_id=rig.identity.hive_id, node_id=node)
    seed = TrailSeeder(local, rig.clock, identity)
    episode = await cell_episode(rig.seed, seed, cell)
    await seed.injection(episode.bee, episode.task)
    await seed.denied(episode.bee)
    await veiled.segments.merge(cell, await local.export_segment(node))  # Its Warden ships it.
    return cell


async def test_a_night_veil_cells_segment_is_read_and_its_alert_stays_in_it() -> None:
    veiled = _veiled()
    cell = await _lured_night_veil_cell(veiled)

    await veiled.rig.bee.tick()

    [report] = veiled.rig.door.filed  # Filed through the Queen's door, as for any Cell.
    assert (report.rule, report.cell_id) == ("injection_then_denial", cell)
    [alert] = await veiled.segments.query(cell, _ALERTS)
    assert alert.payload["report_id"] == report.id
    assert await veiled.durable.query(_ALERTS) == ()  # Nothing of it reached the durable trail.


async def test_every_fact_read_from_a_taken_segment_is_forgotten_with_it() -> None:
    veiled = _veiled()
    rig = veiled.rig
    cell = await _lured_night_veil_cell(veiled)
    watch = TrailWatch(load_guard_rules(), rig.identity.node_id, _HORIZON_S)
    await watch.read(rig.trail, rig.clock.now())
    read = len(watch.facts_of("guard.denied"))

    await veiled.segments.take(cell)  # Teardown: the purge takes the segment whole.
    await watch.read(rig.trail, rig.clock.now())

    assert read == 1
    assert watch.facts_of("guard.denied") == []
    assert watch.facts_of("guard.injection_suspected") == []
