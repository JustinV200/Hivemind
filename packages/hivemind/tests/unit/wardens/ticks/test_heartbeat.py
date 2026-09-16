"""Tests for hivemind.wardens.ticks.heartbeat: check_sub_bee_context and compact_view's own cap.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/heartbeat.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.heartbeat for the module under test.
"""

from __future__ import annotations

import asyncio
from typing import cast

from builders.wardens import make_warden_deps
from builders.workers import make_assignment

from hivemind.memory.thresholds import MAX_COMPACT_VIEW_CHARS
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.heartbeat import check_sub_bee_context, compact_view
from hivemind.wardens.warden import Warden
from hivemind.workers.runtime import WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.codec import Codec
from waggle.ids import new_worker_id
from waggle.messages.supervision import ContextTelemetry, Intervene, InterventionAction
from waggle.transport.memory import MemoryTransport


def _telemetry(fraction: float, **overrides: object) -> ContextTelemetry:
    window = 1_000
    fields: dict[str, object] = {
        "tokens_used": int(window * fraction),
        "context_window": window,
        "goal": "Working the task.",
        "last_actions": (),
        "blockers": (),
        "spend": 0.0,
    }
    fields.update(overrides)
    return ContextTelemetry(**fields)


def _insert_sub_bee(warden: Warden, telemetry: ContextTelemetry | None) -> MemoryTransport:
    """Insert a minimal SubBee (never a running WorkerRuntime) and return its own link's peer.

    Drives `hivemind.wardens.ticks.heartbeat.check_sub_bee_context` directly, the same "internals
    over the whole tick loop" shape `tests/unit/wardens/test_warden_spawn_and_accept.py::
    test_a_grant_shrunk_below_current_usage_raises_grant_exceeded` already uses for a sibling
    concern (GRANT_EXCEEDED): `runtime`/`runtime_task` are never read by the function under test,
    so a real WorkerRuntime is not needed to exercise it.
    """
    warden_end, sub_bee_end = MemoryTransport.pair(Codec(), Codec())
    worker_id = new_worker_id(warden._deps.clock)
    assignment = make_assignment(clock=warden._deps.clock)
    sub_bee = SubBee(
        worker_id=worker_id,
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=1,
        state=WorkerState.RUNNING,
        binding="worker",
        last_handoff=None,
        link=warden_end,
        runtime=cast(WorkerRuntime, None),
        runtime_task=asyncio.ensure_future(asyncio.sleep(0)),
        last_telemetry=telemetry,
    )
    warden._sub_bees[worker_id] = sub_bee
    return sub_bee_end


async def test_check_sub_bee_context_sends_nothing_below_both_thresholds() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    sub_bee_end = _insert_sub_bee(warden, _telemetry(0.1))

    await check_sub_bee_context(warden)

    receive = sub_bee_end.receive()
    task = asyncio.ensure_future(anext(receive))
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()


async def test_check_sub_bee_context_sends_compact_past_compact_at() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    sub_bee_end = _insert_sub_bee(warden, _telemetry(0.6))

    await check_sub_bee_context(warden)

    envelope = await anext(sub_bee_end.receive())
    payload = cast(Intervene, envelope.payload)
    assert payload.action is InterventionAction.COMPACT


async def test_check_sub_bee_context_sends_handoff_past_handoff_threshold() -> None:
    deps, _queen_end, warden_id = make_warden_deps(handoff_threshold=0.66)
    warden = Warden(warden_id, deps)
    sub_bee_end = _insert_sub_bee(warden, _telemetry(0.9))

    await check_sub_bee_context(warden)

    envelope = await anext(sub_bee_end.receive())
    payload = cast(Intervene, envelope.payload)
    assert payload.action is InterventionAction.HANDOFF


async def test_check_sub_bee_context_skips_a_sub_bee_with_no_telemetry_yet() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    sub_bee_end = _insert_sub_bee(warden, None)

    await check_sub_bee_context(warden)

    receive = sub_bee_end.receive()
    task = asyncio.ensure_future(anext(receive))
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()


def test_compact_view_stays_under_the_size_cap_for_a_full_telemetry() -> None:
    telemetry = _telemetry(
        0.5,
        goal="g" * 999,
        last_actions=tuple("a" * 199 for _ in range(10)),
        blockers=tuple("b" * 499 for _ in range(10)),
    )

    view = compact_view(telemetry)

    total = len(view.goal) + len(view.progress) + sum(len(d) for d in view.decisions)
    total += sum(len(t) for t in view.open_threads)
    assert total <= MAX_COMPACT_VIEW_CHARS
