"""Tests for the Guard Bee's attribution: a Cell's node counts only for what its own Cell owns.

A Virtual Cell's Warden ships its own trail segment, so a compromised Cell can record anything on
its own node. Every test here attaches Cells as the Queen records it (`warden.spawned` naming the
node each link proved) and places their tasks as she does, then has a Cell's node record the
injection correlation (a flag, then a denial). About its own bee the correlation is counted and
reported against that Cell. About another Cell's bee it is never counted as what it says: the
Guard Bee reports the recording node's Cell for forging (`subject_forgery`, CRITICAL, isolate) and
names nothing of the Cell it framed. A spawn a Cell records for another Cell's task is evidence
too, and gives the bee no owner; a bee two Cells both claim counts for neither, since nothing says
which claim is true; a node no Cell speaks for is not counted at all; and a task placed on two
Cells in turn is either one's own.

Fits into the Hive:
    Mirrors the attribution in src/hivemind/workers/roles/guard_bee/facts.py and .watch
    (codingrules section 3), over a whole Guard Bee round.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.test_guard_bee_framing for the same over real Virtual Cells.
"""

from __future__ import annotations

from builders.cells import make_identity
from builders.guard_bee import (
    GuardBeeRig,
    TrailSeeder,
    bind_cell,
    cell_episode,
    make_guard_bee,
    seed_episode,
    spawn,
)

from hivemind.guard import GuardAction, GuardConfidence
from hivemind.pheromone import QueenEvent, WorkerEvent
from waggle.ids import new_node_id


def _node(rig: GuardBeeRig) -> TrailSeeder:
    """A seeder recording onto the rig's trail as a fresh node, as a Cell's shipped segment does."""
    node = new_node_id(rig.clock)
    identity = make_identity(rig.clock, hive_id=rig.identity.hive_id, node_id=node)
    return TrailSeeder(rig.trail, rig.clock, identity)


async def _cell(rig: GuardBeeRig) -> tuple[str, TrailSeeder]:
    """A Virtual Cell the Queen attached: its id, and a seeder recording as the node it proved."""
    seed = _node(rig)
    return await bind_cell(rig.seed, seed.identity.node_id), seed


async def _lure(seed: TrailSeeder, bee: str, task: str) -> list[str]:
    """The injection correlation as `seed`'s node records it; the two events' ids."""
    flag = await seed.injection(bee, task)
    denial = await seed.denied(bee)
    return [flag.id, denial.id]


async def test_a_cells_own_records_about_its_own_bee_count_against_it() -> None:
    rig = make_guard_bee()
    cell, seed = await _cell(rig)
    episode = await cell_episode(rig.seed, seed, cell)
    await _lure(seed, episode.bee, episode.task)

    await rig.bee.tick()

    [report] = rig.door.filed
    assert (report.rule, report.cell_id) == ("injection_then_denial", cell)
    assert (report.bee_ids, report.task_ids) == ((episode.bee,), (episode.task,))


async def test_a_cell_naming_another_cells_bee_is_reported_itself_and_the_other_never() -> None:
    rig = make_guard_bee()
    victim_cell, victim_seed = await _cell(rig)
    victim = await cell_episode(rig.seed, victim_seed, victim_cell)
    framer_cell, framer_seed = await _cell(rig)
    forged = await _lure(framer_seed, victim.bee, victim.task)

    await rig.bee.tick()

    [report] = rig.door.filed  # No injection_then_denial about the victim's bee.
    assert (report.rule, report.cell_id) == ("subject_forgery", framer_cell)
    assert (report.recommended, report.confidence) == (
        GuardAction.ISOLATE_CELL,
        GuardConfidence.CRITICAL,
    )
    # The report names the framer and the forged records, nothing of the Cell they framed.
    assert (report.bee_ids, report.task_ids, report.grant_ids) == ((), (), ())
    assert set(report.event_ids) == set(forged)


async def test_a_spawn_forged_for_another_cells_task_is_evidence_and_joins_nothing() -> None:
    rig = make_guard_bee()
    victim_cell, victim_seed = await _cell(rig)
    victim = await cell_episode(rig.seed, victim_seed, victim_cell)
    framer_cell, framer_seed = await _cell(rig)
    stolen = await spawn(framer_seed, victim.task)  # A bee "for" the victim's task.
    for _ in range(5):
        await framer_seed.denied(stolen)  # A denial burst in its name.

    await rig.bee.tick()
    await rig.bee.flush()  # A judged rule's episode ends here; nothing is scripted to answer.

    assert [(r.rule, r.cell_id) for r in rig.door.filed] == [("subject_forgery", framer_cell)]


async def test_a_bee_two_cells_both_claim_counts_for_neither() -> None:
    rig = make_guard_bee()
    victim_cell, victim_seed = await _cell(rig)
    victim = await cell_episode(rig.seed, victim_seed, victim_cell)
    framer_cell, framer_seed = await _cell(rig)
    framer = await cell_episode(rig.seed, framer_seed, framer_cell)
    # The framer claims the victim's bee for its own task: one bee id, two Cells claiming it.
    claim = {"task_id": framer.task, "role": "DRONE"}
    await framer_seed.record(WorkerEvent, "worker.spawned", victim.bee, claim)
    await _lure(framer_seed, victim.bee, framer.task)
    await _lure(victim_seed, victim.bee, victim.task)

    await rig.bee.tick()

    assert rig.door.filed == []  # Nothing on the trail says which claim is true.


async def test_a_node_no_cell_speaks_for_is_not_counted() -> None:
    rig = make_guard_bee()
    stranger = _node(rig)  # Never attached: no warden.spawned names it.
    episode = await seed_episode(stranger)
    await _lure(stranger, episode.bee, episode.task)

    await rig.bee.tick()

    assert (rig.door.filed, await rig.alerts()) == ([], [])


async def test_a_task_placed_on_two_cells_in_turn_is_either_ones_own() -> None:
    rig = make_guard_bee()
    first_cell, first_seed = await _cell(rig)
    episode = await cell_episode(rig.seed, first_seed, first_cell)
    second_cell, second_seed = await _cell(rig)
    # The task is placed again, on the second Cell, which spawns a bee of its own for it.
    await rig.seed.record(QueenEvent, "queen.assigned", episode.task, {"cell_id": second_cell})
    bee = await spawn(second_seed, episode.task)
    await _lure(second_seed, bee, episode.task)

    await rig.bee.tick()

    [report] = rig.door.filed
    assert (report.rule, report.cell_id, report.bee_ids) == (
        "injection_then_denial",
        second_cell,
        (bee,),
    )
