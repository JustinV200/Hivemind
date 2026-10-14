"""Tests for hivemind.wardens.offline.freeze: pausing what cannot think while the Warden is cut off.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/freeze.py (codingrules section 3), driven through a real
    Warden on a device's deps (builders.offline_warden). When the link to the Queen goes the Warden
    moves to OFFLINE, leaves its grants as they are, and tells each sub-bee that has nothing local
    to think with to stop at a Handoff (a Nuc's bees keep going); past `max_offline_s` it clusters
    fully; a Level 0 Warden, which never goes OFFLINE, pauses its bees when its Cell is lost. A bee
    the Warden started for itself (a diagnostic Drone, roadmap 11.9a) is never one of those: it is
    no task of the Queen's to resume.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.freeze for the module under test.
    - builders.offline_warden for the scene these tests run in.
"""

from __future__ import annotations

from datetime import timedelta

from builders.offline_warden import WARDEN_BEAT_S, WORKER_BEAT_S, pausable, start_offline_scene
from builders.quarantine import drain, settle

from hivemind.memory import Handoff
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.wardens import WardenDeps
from hivemind.wardens.spawn import LocalRun
from hivemind.wardens.state import WardenState
from hivemind.workers.state import WorkerState
from waggle.messages.supervision import AlarmKind


async def _events(deps: WardenDeps, kind: str) -> list[PheromoneEvent]:
    """Every trail event of `kind`, in the order it was recorded."""
    return [event for event in await deps.trail.query(TrailQuery()) if event.kind == kind]


async def test_a_cut_link_moves_the_warden_offline_and_pauses_a_bee_with_no_local_model() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed))

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    bee = await scene.wait_paused()

    [held] = scene.outage.held()
    assert held.assignment.task_id == scene.assignment.task_id
    assert held.handoff == bee.last_handoff  # The Handoff the bee wrote as it stopped.
    assert resumed == []  # Nothing restarts it while the Warden is cut off.
    assert scene.warden.state is WardenState.OFFLINE  # Retiring the bee did not leave OFFLINE.
    [event] = await _events(scene.deps, "warden.offline")
    assert event.payload == {
        "reason": "The link to the Queen was lost.",
        "paused": 1,
        "frozen_grants": 1,
    }
    await scene.stop()


async def test_a_bee_whose_model_runs_on_the_device_keeps_going() -> None:
    scene = await start_offline_scene(pausable([]), local_providers=frozenset({"fake"}))
    [bee] = scene.rows()

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(WORKER_BEAT_S)
    await drain()

    assert bee.runtime.state is WorkerState.RUNNING
    assert scene.rows() == [bee] and not scene.outage.has_held
    [event] = await _events(scene.deps, "warden.offline")
    assert event.payload["paused"] == 0
    await scene.stop()


async def test_a_bee_the_warden_started_for_itself_is_never_paused_by_a_cut_or_the_limit() -> None:
    scene = await start_offline_scene(pausable([]), max_offline_s=30.0)
    [bee] = scene.rows()
    bee.local = LocalRun()  # Its model is not local: a task of the Queen's would be paused here.

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(WARDEN_BEAT_S * 4)  # Past max_offline_s: the Warden clusters fully.
    await settle(lambda: scene.outage.is_clustered_fully)
    await drain()

    # It is not a task the Queen could resume from a Handoff, so nothing was held for her.
    assert bee.runtime.state is WorkerState.RUNNING
    assert scene.rows() == [bee] and not scene.outage.has_held
    [event] = await _events(scene.deps, "warden.offline")
    assert event.payload["paused"] == 0
    assert await _events(scene.deps, "warden.clustered") == []
    await scene.stop()


async def test_shared_grants_are_left_exactly_as_they_were() -> None:
    scene = await start_offline_scene(pausable([]))
    grants = dict(scene.warden._grants)

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(10 * WARDEN_BEAT_S)  # The grant's expiry passes while cut off.
    await drain()

    # Nothing renewed it, nothing dropped it: an expiry while offline is not a revocation.
    assert scene.warden._grants == grants
    assert scene.outage.frozen_grants == {gid: grant.revision for gid, grant in grants.items()}
    await scene.stop()


async def test_the_wardens_own_context_check_does_not_order_a_second_handoff() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed, full_context=True))
    [bee] = scene.rows()
    scene.clock.advance(
        WORKER_BEAT_S
    )  # The bee's heartbeat carries its full context to the Warden.
    await settle(lambda: bee.last_telemetry is not None)

    scene.cut()
    await settle(lambda: scene.outage.has_held)
    scene.clock.advance(WARDEN_BEAT_S)  # The Warden's own heartbeat, and the context check on it.
    await drain()

    # Its context check would have marked the bee for a successor started while cut off.
    assert bee.handoff_ordered is False
    await scene.wait_paused(bee)
    assert resumed == [] and scene.rows() == []
    await scene.stop()


async def test_an_outage_past_the_limit_clusters_the_warden_fully() -> None:
    scene = await start_offline_scene(
        pausable([]), local_providers=frozenset({"fake"}), max_offline_s=30.0
    )
    [bee] = scene.rows()
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    assert bee.runtime.state is WorkerState.RUNNING  # A local model keeps it going, for a while.

    scene.clock.advance(WARDEN_BEAT_S * 4)  # Past max_offline_s; the next heartbeat tick notices.
    await settle(lambda: scene.warden.state is WardenState.CLUSTERED)
    await scene.wait_paused()

    assert scene.outage.is_clustered_fully
    assert scene.warden._clustered_tasks == {scene.assignment.task_id}
    [event] = await _events(scene.deps, "warden.clustered")
    assert event.payload["paused"] == 1
    assert scene.warden.state is WardenState.CLUSTERED  # Retiring its bee did not undo it.
    await scene.stop()


async def test_the_limit_clusters_the_warden_when_it_passes_and_not_at_the_next_heartbeat() -> None:
    limit = (
        WARDEN_BEAT_S / 2
    )  # It passes between two heartbeats, so only the Warden's own wake can meet it.
    scene = await start_offline_scene(pausable([]), max_offline_s=limit)
    cut_at = scene.clock.now()
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)

    scene.clock.advance(limit - 1.0)
    await drain()
    early = scene.outage.is_clustered_fully
    scene.clock.advance(
        1.0
    )  # Nothing is sent or nudged: the limit is the only thing that has come.
    await settle(lambda: scene.warden.state is WardenState.CLUSTERED)

    [event] = await _events(scene.deps, "warden.clustered")
    assert not early and event.at - cut_at == timedelta(seconds=limit)
    await scene.stop()


async def test_the_tick_is_told_when_the_limit_falls_and_nothing_once_it_has_passed() -> None:
    scene = await start_offline_scene(pausable([]), max_offline_s=30.0)
    cut_at = scene.clock.now()
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    ahead = scene.outage.due_at

    scene.clock.advance(30.0)
    await settle(lambda: scene.outage.due_at is None)

    # Acted on, so there is nothing left to wake the tick for: it would find nothing and wake again.
    assert ahead == cut_at + timedelta(seconds=30.0)
    assert scene.outage.is_clustered_fully
    await scene.stop()


async def test_an_idle_warden_goes_offline_and_has_nothing_to_cluster() -> None:
    scene = await start_offline_scene(pausable([]), assign=False, max_offline_s=30.0)

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(WARDEN_BEAT_S * 4)
    await settle(lambda: scene.outage.is_clustered_fully)

    assert scene.warden.state is WardenState.OFFLINE  # No task to cluster: nothing to announce.
    assert await _events(scene.deps, "warden.clustered") == []
    await scene.stop()


async def test_the_queens_own_clustering_is_left_alone_by_a_cut() -> None:
    # CLUSTERED has no edge to OFFLINE: a Warden the Queen already paused stays as it is, and the
    # cut only starts the outage's clock.
    scene = await start_offline_scene(pausable([]))
    scene.warden._state = WardenState.CLUSTERED
    scene.warden._clustered_tasks.add(scene.assignment.task_id)

    scene.cut()
    await settle(lambda: scene.outage.cut_since is not None)

    assert scene.warden.state is WardenState.CLUSTERED
    assert not scene.outage.has_held  # The Queen's pause is hers to resume, not the outage's.
    await scene.stop()


async def test_a_lost_cell_pauses_the_bees_of_a_level_zero_warden_without_going_offline() -> None:
    scene = await start_offline_scene(pausable([]), remote=False)

    await scene.warden.cell_lost("the link closed")
    bee = await scene.wait_paused()

    [held] = scene.outage.held()
    assert held.assignment.task_id == scene.assignment.task_id
    assert held.handoff == bee.last_handoff
    assert scene.warden.state is not WardenState.OFFLINE  # A Level 0 Warden never goes OFFLINE.
    alarm = await scene.queen.wait_for_alarm()
    assert alarm.kind is AlarmKind.CELL_UNREACHABLE  # The Alarm wave 2C raises is still raised.
    await scene.stop()
