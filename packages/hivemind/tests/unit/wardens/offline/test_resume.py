"""Tests for hivemind.wardens.offline.resume: leaving an outage and starting paused tasks again.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/resume.py (codingrules section 3), driven through a real
    Warden on a device's deps (builders.offline_warden). A link that comes back takes the Warden to
    ACTIVE when a task was paused (it starts again from its Handoff, on the same attempt, once the
    Queen has had a moment to speak first) and to WATCH when none was; the Queen's word on a held
    task (a cancel, a pause, a new attempt) wins over the Warden's own hold; a Warden that
    clustered fully stays CLUSTERED for the Queen to resume; and a Level 0 device Warden whose Cell
    came back leases it again before it resumes.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.resume for the module under test.
    - builders.offline_warden for the scene these tests run in.
"""

from __future__ import annotations

import asyncio

from builders.basket import make_manifest
from builders.offline_warden import (
    WARDEN_BEAT_S,
    OfflineScene,
    pausable,
    start_offline_scene,
)
from builders.quarantine import drain, make_grant_issued, settle

from hivemind.basket import TransferSender
from hivemind.basket.transfer import BytesChunkSource
from hivemind.memory import Handoff
from hivemind.pheromone import TrailQuery
from hivemind.wardens.offline import RESUME_SETTLE_S
from hivemind.wardens.state import WardenState
from hivemind.wardens.ticks.basket.state import Outgoing
from waggle.ids import new_cell_id, new_grant_id
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskCancel, TaskPause


def _holds(scene: OfflineScene) -> bool:
    """Whether the outage holds a task, read through a call so mypy keeps no narrowing of it."""
    return scene.outage.has_held


async def _kinds(scene: OfflineScene) -> list[str]:
    """Every event kind on the Warden's trail, in the order it was recorded."""
    return [event.kind for event in await scene.deps.trail.query(TrailQuery())]


async def _paused_and_back(scene: OfflineScene) -> None:
    """Cut the link, let the bee stop at its Handoff, bring the link back; the settle is pending."""
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    await scene.wait_paused()
    scene.restore()
    await settle(lambda: scene.warden.state is not WardenState.OFFLINE)


async def _past_the_settle(scene: OfflineScene) -> None:
    """Let the pause the Queen is given to speak first run out, and wake the tick."""
    scene.clock.advance(RESUME_SETTLE_S + 0.1)
    scene.outage.nudge()


async def test_a_returning_link_resumes_the_paused_task_from_its_handoff() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed))
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    bee = await scene.wait_paused()
    checkpoint = bee.last_handoff

    scene.restore()
    await settle(lambda: scene.warden.state is WardenState.WATCH)  # No sub-bee runs yet.
    await _past_the_settle(scene)
    await settle(lambda: bool(resumed))

    [successor] = scene.rows()
    assert successor.worker_id != bee.worker_id
    assert successor.attempt == scene.assignment.attempt  # The same attempt carried on.
    assert successor.assignment.resume_from == checkpoint
    assert resumed[0].written_by == "the first bee"
    assert not _holds(scene) and scene.outage.cut_since is None
    assert scene.warden._sub_bee_slots.in_use == 1
    states = ("warden.offline", "warden.watch", "warden.active")
    assert [k for k in await _kinds(scene) if k in states][-3:] == list(states)
    await scene.stop()


async def test_the_task_waits_a_moment_for_the_queen_to_speak_first() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed))
    await _paused_and_back(scene)

    scene.outage.nudge()  # Another tick, but the Queen has had no time yet.
    await drain()
    assert resumed == [] and scene.rows() == [] and _holds(scene)

    await _past_the_settle(scene)
    await settle(lambda: bool(resumed))
    assert not _holds(scene)
    await scene.stop()


async def test_a_warden_that_was_idle_returns_to_watch() -> None:
    scene = await start_offline_scene(pausable([]), assign=False)
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)

    scene.restore()
    await settle(lambda: scene.warden.state is WardenState.WATCH)

    states = [k for k in await _kinds(scene) if k.startswith("warden.")]
    assert states[-2:] == ["warden.offline", "warden.watch"]
    assert scene.outage.cut_since is None
    await scene.stop()


async def test_a_task_the_queen_cancels_while_held_is_not_started_again() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed))
    await _paused_and_back(scene)

    await scene.queen.send(
        TaskCancel(task_id=scene.assignment.task_id, grace_s=0.0, reason="Re-placed.")
    )
    await settle(lambda: not _holds(scene))
    await _past_the_settle(scene)
    await drain()

    assert resumed == [] and scene.rows() == []
    await scene.stop()


async def test_a_task_the_queen_pauses_while_held_is_hers_to_resume() -> None:
    scene = await start_offline_scene(pausable([]))
    await _paused_and_back(scene)
    handoff = Intervene(
        action=InterventionAction.HANDOFF,
        subject=None,
        task_id=scene.assignment.task_id,
        slot=None,
        binding=None,
        alarm_id=None,
        reason="Clustering.",
    )

    await scene.queen.send(handoff)
    await scene.queen.send(TaskPause(task_id=scene.assignment.task_id, reason="Clustering."))
    await settle(lambda: not _holds(scene))

    assert scene.assignment.task_id in scene.warden._clustered_tasks  # Her bookkeeping, not ours.
    await scene.stop()


async def test_a_new_attempt_from_the_queen_replaces_the_held_task() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed))
    await _paused_and_back(scene)
    retry = scene.assignment.model_copy(
        update={"attempt": 2, "grant_id": new_grant_id(scene.clock)}
    )

    await scene.queen.send(make_grant_issued(scene.clock, retry))
    await scene.queen.send(retry)
    await settle(lambda: [row.attempt for row in scene.rows()] == [2])
    await _past_the_settle(scene)
    await drain()

    assert not _holds(scene)  # Her attempt replaced it; the Warden starts no second one.
    assert len(scene.rows()) == 1
    await scene.stop()


async def test_a_warden_that_clustered_fully_leaves_its_tasks_to_the_queen() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed), max_offline_s=30.0)
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(WARDEN_BEAT_S * 4)
    await settle(lambda: scene.warden.state is WardenState.CLUSTERED)
    bee = await scene.wait_paused()

    scene.restore()
    await settle(lambda: scene.outage.cut_since is None)
    await _past_the_settle(scene)
    await drain()

    assert scene.warden.state is WardenState.CLUSTERED  # Still paused: the Queen resumes it.
    assert not _holds(scene) and resumed == [] and scene.rows() == []
    # Her fresh TaskAssign is the existing Clustering resume; its Handoff is in this Warden's store.
    resume = scene.assignment.model_copy(
        update={
            "attempt": 2,
            "grant_id": new_grant_id(scene.clock),
            "resume_from": bee.last_handoff,
        }
    )
    await scene.queen.send(make_grant_issued(scene.clock, resume))
    await scene.queen.send(resume)
    await settle(lambda: bool(resumed) and scene.warden.state is WardenState.ACTIVE)
    await scene.stop()


async def test_the_settle_wakes_the_tick_by_the_clock_alone() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed))
    await _paused_and_back(scene)
    await settle(lambda: scene.outage.due_at is not None)  # The pass named its own wake.

    scene.clock.advance(RESUME_SETTLE_S + 0.1)  # No nudge, and far short of the heartbeat.

    await settle(lambda: bool(resumed))
    assert not _holds(scene)
    await scene.stop()


async def test_a_returning_link_offers_a_half_shipped_blob_again() -> None:
    scene = await start_offline_scene(pausable([]), assign=False)
    manifest = make_manifest()
    sender = TransferSender(BytesChunkSource(b"x" * manifest.blob.size_bytes))
    offer = sender.offer(manifest, new_cell_id(scene.clock), "cut mid-way")
    # Standing when the link went: its offer was answered and a chunk was in flight.
    scene.warden._basket.outgoing[offer.sha256] = Outgoing(
        sender, offer, asyncio.get_running_loop().create_future()
    )
    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)

    scene.restore()
    await scene.queen.pump_until(lambda: bool(scene.queen.basket_offers))

    [again] = scene.queen.basket_offers
    assert again.sha256 == offer.sha256 and again.reason == "cut mid-way"
    assert offer.sha256 in scene.warden._basket.outgoing  # Still standing for the Queen's ack.
    await scene.stop()
