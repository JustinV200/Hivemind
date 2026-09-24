"""Tests for hivemind.workers.roles.guard_bee.watch: rebuilt on start, followed per node.

The first read rebuilds every kind over its own horizon and hands back the Guard Bee's own alerts
(never another node's); later reads keep each event once; a segment a known Virtual Cell's Warden
ships late, older than what was already read, is still read; so is a new node's first segment,
though every event in it is older than what was read; so is a new node's older backlog, the
moment the node is first heard from; and what every horizon has passed is let go.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/watch.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.watch for TrailWatch.
    - tests.unit.workers.roles.guard_bee.test_bee for a restart over the whole Guard Bee.
"""

from __future__ import annotations

from datetime import timedelta

from builders.cells import make_identity
from builders.guard_bee import TrailSeeder, make_guard_bee, seed_episode

from hivemind.pheromone import GuardEvent, MemoryPheromoneTrail, TrailQuery, TrailSegment
from hivemind.workers.roles.guard_bee import LATE_LAG_S, TrailWatch, load_guard_rules
from waggle.clock import FakeClock
from waggle.ids import new_node_id

_HORIZON_S = 3_600.0  # The own-alert horizon every watch here is built with.


def _watch(node: str) -> TrailWatch:
    return TrailWatch(load_guard_rules(), node, _HORIZON_S)


async def _segment(clock: FakeClock, count: int, start_offset_s: float) -> TrailSegment:
    """A Virtual Cell Warden's local segment: `count` denials of one of its bees, from an offset."""
    cell_clock = FakeClock(clock.now() + timedelta(seconds=start_offset_s))
    local = MemoryPheromoneTrail(cell_clock)
    identity = make_identity(cell_clock, hive_id=None, node_id=new_node_id(cell_clock))
    seed = TrailSeeder(local, cell_clock, identity)
    episode = await seed_episode(seed)
    for _ in range(count):
        cell_clock.advance(0.5)
        await seed.denied(episode.bee)
    return await local.export_segment(identity.node_id)


async def test_the_rebuild_reads_each_kind_over_its_own_horizon() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.denied(episode.bee)
    proposal = await rig.seed.proposed(episode.task, episode.cell, "SCRATCH_WRITE")
    await rig.seed.capping(proposal, "capping.audited", tier="SCRATCH_WRITE", outcome="REJECT")
    rig.clock.advance(20 * 60.0)  # Past guard.denied's 15 minutes, well inside a day of audits.
    watch = _watch(rig.identity.node_id)

    await watch.read(rig.trail, rig.clock.now())

    assert watch.facts_of("guard.denied") == []
    assert len(watch.facts_of("capping.audited")) == 1


async def test_the_rebuild_hands_back_only_this_nodes_own_alerts() -> None:
    rig = make_guard_bee()
    own = await rig.seed.record(GuardEvent, "guard.alert", rig.identity.hive_id, {"rule": "x"})
    other = TrailSeeder(
        rig.trail, rig.clock, make_identity(rig.clock, hive_id=rig.identity.hive_id)
    )
    await other.record(GuardEvent, "guard.alert", rig.identity.hive_id, {"rule": "forged"})
    watch = _watch(rig.identity.node_id)

    alerts = await watch.read(rig.trail, rig.clock.now())

    assert [alert.id for alert in alerts] == [own.id]


async def test_every_event_is_kept_once_across_rounds() -> None:
    rig = make_guard_bee()
    watch = _watch(rig.identity.node_id)
    await watch.read(rig.trail, rig.clock.now())
    episode = await seed_episode(rig.seed)
    await rig.seed.denied(episode.bee)

    for _ in range(3):
        await watch.read(rig.trail, rig.clock.now())

    assert len(watch.facts_of("guard.denied")) == 1


async def test_a_known_nodes_late_segment_is_read_though_older_than_what_was_read() -> None:
    rig = make_guard_bee()
    watch = _watch(rig.identity.node_id)
    await watch.read(rig.trail, rig.clock.now())
    first = await _segment(rig.clock, 1, start_offset_s=1.0)
    await rig.trail.merge_segment(first)
    rig.clock.advance(2.0)
    await watch.read(rig.trail, rig.clock.now())  # The Cell's node is known from here on.
    # The Hive Stand moves on; the Cell's next segment arrives late, stamped before it.
    rig.clock.advance(60.0)
    await rig.seed.denied((await seed_episode(rig.seed)).bee)
    await watch.read(rig.trail, rig.clock.now())
    late = await _segment(rig.clock, 3, start_offset_s=-50.0)
    await rig.trail.merge_segment(_renode(late, first.node_id))

    await watch.read(rig.trail, rig.clock.now())

    assert len(watch.facts_of("guard.denied")) == 1 + 1 + 3


async def test_a_new_nodes_first_segment_is_read_though_older_than_what_was_read() -> None:
    rig = make_guard_bee()
    watch = _watch(rig.identity.node_id)
    await watch.read(rig.trail, rig.clock.now())
    rig.clock.advance(30.0)
    await rig.seed.denied((await seed_episode(rig.seed)).bee)  # The Hive Stand moves on.
    await watch.read(rig.trail, rig.clock.now())
    # A new Virtual Cell's first segment lands a heartbeat late: every event older than the last.
    first = await _segment(rig.clock, 3, start_offset_s=-20.0)
    await rig.trail.merge_segment(first)

    await watch.read(rig.trail, rig.clock.now())

    assert len(watch.facts_of("guard.denied")) == 1 + 3


async def test_a_new_nodes_backlog_is_read_the_moment_it_is_first_heard_from() -> None:
    rig = make_guard_bee()
    watch = _watch(rig.identity.node_id)
    rig.clock.advance(LATE_LAG_S * 3)
    await watch.read(rig.trail, rig.clock.now())
    # All older than the rebuild, and further back than any round reads again.
    backlog = await _segment(rig.clock, 4, start_offset_s=-(LATE_LAG_S + 100.0))
    await rig.trail.merge_segment(backlog)
    await watch.read(rig.trail, rig.clock.now())
    unheard = len(watch.facts_of("guard.denied"))

    # Its Warden ships one more, fresh: the node is heard from, and its backlog read with it.
    fresh = await _segment(rig.clock, 1, start_offset_s=1.0)
    await rig.trail.merge_segment(_renode(fresh, backlog.node_id))
    rig.clock.advance(2.0)
    await watch.read(rig.trail, rig.clock.now())

    assert unheard == 0
    assert len(watch.facts_of("guard.denied")) == 4 + 1


async def test_what_every_horizon_has_passed_is_let_go() -> None:
    rig = make_guard_bee()
    watch = _watch(rig.identity.node_id)
    await watch.read(rig.trail, rig.clock.now())
    episode = await seed_episode(rig.seed)
    await rig.seed.denied(episode.bee)
    await watch.read(rig.trail, rig.clock.now())

    rig.clock.advance(16 * 60.0)
    await watch.read(rig.trail, rig.clock.now())

    assert watch.facts_of("guard.denied") == []
    assert await rig.trail.query(TrailQuery(kind="guard.denied")) != ()  # The trail keeps it.


def _renode(segment: TrailSegment, node: str) -> TrailSegment:
    """Return `segment` re-stamped as recorded on `node`: the same Cell's next export."""
    events = tuple(event.model_copy(update={"node_id": node}) for event in segment.events)
    return TrailSegment(node_id=node, exported_at=segment.exported_at, events=events)
