"""Tests for a quarantine a Warden orders itself: its own policy row, for its own sub-bee.

ADR-0035: "a Warden may apply it to its own sub-bee by its own policy row (it may already cancel
one), and the Queen is told either way." A sub-bee raises a SECURITY Alarm (a scanner correlation
noted on its telemetry); this Warden's policy maps SECURITY to QUARANTINE; the one code path runs
with the Warden itself as the orderer, from the bee's own `worker.spawned` (the Alarm names no
event), the triggering Alarm is recorded handled, and the Queen hears one SECURITY Alarm, the
Warden's own, never the bee's forwarded. A stalled sub-bee reaches the same path through the tick's
own dispatch when a row names QUARANTINE for WORKER_STALLED.

Fits into the Hive:
    Mirrors src/hivemind/wardens/quarantine/path.py and order.py (codingrules section 3); split by
    feature (14.2) from test_path.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.quarantine.order for own_policy_order.
    - hivemind.wardens.autopilot.table for the QUARANTINE row's mapping.
"""

from __future__ import annotations

from typing import cast

from builders.quarantine import (
    HeldRole,
    QuarantineScene,
    drain,
    queen_hears,
    start_scene,
    stop_scene,
)

from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.supervision import AlarmKind as HiveAlarmKind
from hivemind.supervision import EscalationPolicy, PolicyAction, PolicyRule
from waggle.clock import FakeClock
from waggle.messages.supervision import AlarmKind

_HEARTBEAT_S = 5.0  # make_warden_deps' own heartbeat interval, for the Warden and its bees.


def _quarantine_on(kind: HiveAlarmKind) -> EscalationPolicy:
    """A Warden policy whose one row quarantines the bee on an Alarm of `kind`."""
    return EscalationPolicy(
        rules=(PolicyRule(kind=kind, min_attempts=1, action=PolicyAction.QUARANTINE),),
        default=PolicyAction.ESCALATE,
    )


async def _events(scene: QuarantineScene, kind: str) -> list[PheromoneEvent]:
    """Every event of `kind` on the scene's trail."""
    return list(await scene.deps.trail.query(TrailQuery(kind=kind)))


async def test_a_wardens_own_policy_row_quarantines_its_sub_bee_and_tells_the_queen() -> None:
    policy = _quarantine_on(HiveAlarmKind.SECURITY)
    scene = await start_scene(HeldRole(note=AlarmKind.SECURITY), policy=policy)
    bee = scene.warden.sub_bees[0].worker_id

    # The bee's runtime sends the noted Alarm at its next tick, which its heartbeat wakes.
    cast(FakeClock, scene.deps.clock).advance(_HEARTBEAT_S)
    await queen_hears(scene, lambda: bool(scene.queen.alarms))

    [spawned] = await _events(scene, "worker.spawned")
    [intervened] = await _events(scene, "warden.intervened")
    assert intervened.payload["ordered_by"] == "warden"
    assert intervened.payload["worker_id"] == bee
    # The Alarm named no event, so the bee is suspect from the start of its attempt.
    assert intervened.payload["suspect_episode_id"] == spawned.id
    [raised_by_bee] = [
        e
        for e in await _events(scene, "alarm.raised")
        if e.subject_id == intervened.payload.get("alarm_id")
    ]
    handled = await _events(scene, "alarm.handled")
    assert [e.payload["action"] for e in handled if e.subject_id == raised_by_bee.subject_id] == [
        "QUARANTINE"
    ]
    # Told either way: one SECURITY Alarm, the Warden's own, and the task held.
    [alarm] = scene.queen.alarms
    assert alarm.kind is AlarmKind.SECURITY and alarm.origin == scene.warden._warden_id
    assert alarm.alarm_id != raised_by_bee.subject_id
    assert [held.task_id for held in scene.queen.progress] == [scene.assignment.task_id]
    assert scene.role.cancelled and scene.warden.sub_bees == ()
    await stop_scene(scene)


async def test_a_stalled_sub_bee_is_quarantined_by_the_same_path_when_a_row_says_so() -> None:
    policy = _quarantine_on(HiveAlarmKind.WORKER_STALLED)
    # A bee that never heartbeats within the Warden's three intervals is stalled.
    scene = await start_scene(policy=policy, worker_heartbeat_interval_s=1_000.0)
    clock = cast(FakeClock, scene.deps.clock)

    # One Warden heartbeat per advance; drained first so its next deadline is armed each time.
    for _ in range(scene.deps.missed_heartbeats_before_stalled):
        await drain()
        clock.advance(_HEARTBEAT_S)
    await queen_hears(scene, lambda: bool(scene.queen.alarms))

    [intervened] = await _events(scene, "warden.intervened")
    [stalled] = [
        e
        for e in await _events(scene, "alarm.raised")
        if e.subject_id == intervened.payload["alarm_id"]
    ]
    assert stalled.payload["kind"] == HiveAlarmKind.WORKER_STALLED.value
    assert scene.queen.alarms[-1].kind is AlarmKind.SECURITY
    assert scene.warden.sub_bees == ()
    await stop_scene(scene)
