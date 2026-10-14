"""Tests for hivemind.wardens.offline.wait: the request a held task files when its Warden is cut.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/wait.py (codingrules section 3), driven through a real
    Warden on a device's deps (builders.offline_warden). When the link goes, the sub-bee that has no
    local model to think with is paused and its task held; ADR-0053 says such a task "files a
    ForageRequest, which waits in the outbox". The scene's Queen link is the in-memory pair, so
    what the Queen's end collects is what the offline link would have queued. A bee that keeps
    going on the device waits for nothing, and a Warden that only clusters fully asks nothing more.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.wait for the module under test.
    - hivemind.wardens.offline.policy for the table that queues `forage.request` while cut off.
    - builders.offline_warden for the scene these tests run in.
"""

from __future__ import annotations

import asyncio

from builders.offline_warden import (
    WARDEN_BEAT_S,
    OfflineScene,
    pausable,
    start_offline_scene,
)
from builders.quarantine import drain, settle

from hivemind.forage import Tempo, grade_floor
from hivemind.wardens.state import WardenState
from waggle.messages.forage import ForageRequestKind

_WAIT_S = 5.0  # A request that is never filed fails the test instead of blocking the suite.


async def _request_filed(scene: OfflineScene) -> None:
    """Wait, bounded, until the Queen's end has collected a ForageRequest."""
    await asyncio.wait_for(
        scene.queen.pump_until(lambda: bool(scene.queen.forage_requests)), timeout=_WAIT_S
    )


async def test_a_task_held_for_want_of_a_hosted_model_files_a_binding_request() -> None:
    scene = await start_offline_scene(pausable([]))

    scene.cut()
    await _request_filed(scene)
    await scene.wait_paused()

    [request] = scene.queen.forage_requests
    assignment = scene.assignment
    assert request.kind is ForageRequestKind.BINDING
    assert request.grant_id == assignment.grant_id and request.task_id == assignment.task_id
    # The slot the task runs on and the grade its tempo asks of a model: what it waits for.
    assert request.wanted.slot == assignment.slot
    assert request.wanted.minimum_grade == grade_floor(Tempo.from_wire(assignment.tempo).accuracy)
    assert request.tempo == assignment.tempo
    await scene.stop()


async def test_the_request_asks_for_nothing_a_grant_could_grow_by() -> None:
    scene = await start_offline_scene(pausable([]))

    scene.cut()
    await _request_filed(scene)
    await scene.wait_paused()

    [request] = scene.queen.forage_requests
    wanted = request.wanted
    # A cut-off Warden cannot grow (codingrules 15): the request names a need, it takes nothing.
    assert (wanted.seats, wanted.sub_bees, wanted.spend, wanted.tokens) == (0, 0, 0.0, 0)
    assert wanted.source_id is None
    await scene.stop()


async def test_a_bee_that_keeps_going_on_the_device_files_no_request() -> None:
    scene = await start_offline_scene(pausable([]), local_providers=frozenset({"fake"}))

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(WARDEN_BEAT_S)
    await drain()

    assert scene.queen.forage_requests == []  # Its task waits for nothing: it is still running.
    await scene.stop()


async def test_clustering_fully_files_no_request_for_a_bee_that_ran_until_then() -> None:
    scene = await start_offline_scene(
        pausable([]), local_providers=frozenset({"fake"}), max_offline_s=30.0
    )

    scene.cut()
    await settle(lambda: scene.warden.state is WardenState.OFFLINE)
    scene.clock.advance(WARDEN_BEAT_S * 4)  # Past max_offline_s: everything left is paused.
    await settle(lambda: scene.outage.is_clustered_fully)
    await drain()

    # The task is the Queen's to resume now; it was not held for want of a hosted model.
    assert scene.queen.forage_requests == []
    await scene.stop()
