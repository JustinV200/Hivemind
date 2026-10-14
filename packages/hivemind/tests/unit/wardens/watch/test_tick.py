"""Tests for hivemind.wardens.watch.tick: when a Warden samples and patrols, and when it does not.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/tick.py (codingrules section 3). The Warden is real
    (`builders.wardens.make_warden_deps` over fakes) with a `FakeClock` the test moves, a
    `StubProbe` for the Cell and a scripted `FakeLLMProvider` for the Patrol's episode. Most tests
    call `watch_tick` directly, as the Warden's own tick does after it has settled its state; the
    last drives the Warden's real run loop to prove the call is wired in.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.tick for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import timedelta

import pytest
from builders.quarantine import make_grant_issued
from builders.wardens import QueenEnd, make_warden_deps
from builders.watch import (
    StubProbe,
    make_observation,
    make_process,
    make_resources,
    make_watch_deps,
)
from builders.workers import ScriptedWorker, make_assignment

from hivemind.cell import CellSession
from hivemind.guard import CapabilitySet
from hivemind.llm import FakeLLMProvider, ProviderCapabilities, text_response
from hivemind.pheromone import TrailQuery
from hivemind.wardens import ticks
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from hivemind.wardens.watch import WatchDeps
from hivemind.wardens.watch.tick import watch_tick
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.errors import WaggleError
from waggle.messages.supervision import AlarmKind
from waggle.messages.swarm import Observation
from waggle.messages.task import TaskAssign

_INTERVAL, _PATROL = 60.0, 600.0


def _sample(clock: FakeClock, name: str = "worker", share: float = 0.1) -> Observation:
    """An Observation worth keeping, taken now."""
    return make_observation(
        clock.now(),
        processes=[make_process(1, name, share, started_at=clock.now())],
        resources=make_resources(),
    )


async def _idle(
    probe: StubProbe,
    *,
    replies: tuple[str, ...] = (),
    watch: WatchDeps | None = None,
    clock: FakeClock | None = None,
) -> tuple[Warden, QueenEnd, WatchDeps]:
    """A started Warden settled into WATCH over `probe`, its Queen's end, and its WatchDeps."""
    clock = clock if clock is not None else FakeClock()
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(*(text_response(reply) for reply in replies))
    watch = watch or make_watch_deps(probe, interval_s=_INTERVAL, patrol_interval_s=_PATROL)
    deps, queen_end, warden_id = make_warden_deps(clock, fake_provider=provider, watch=watch)
    warden = Warden(warden_id, deps)
    await warden.start()
    await ticks.assign.settle_after_tick(warden)  # No sub-bee: ACTIVE settles to WATCH.
    assert warden.state is WardenState.WATCH
    return warden, queen_end, watch


# ──────────────────────────────────────────────────────────────────────────────
# Sampling: when, and when not
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_warden_built_without_watch_deps_looks_at_nothing() -> None:
    deps, _queen, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    await ticks.assign.settle_after_tick(warden)

    await watch_tick(warden)  # Nothing to do, and nothing to fail.

    assert deps.watch is None


async def test_the_first_sample_is_taken_at_once_and_the_next_one_interval_later() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, _watch = await _idle(probe, clock=clock)

    await watch_tick(warden)
    await watch_tick(warden)  # Same instant: not due again.
    assert len(probe.calls) == 1

    clock.advance(_INTERVAL - 1)
    await watch_tick(warden)
    assert len(probe.calls) == 1

    clock.advance(1)
    await watch_tick(warden)
    assert len(probe.calls) == 2


async def test_nothing_is_watched_while_the_warden_is_not_in_watch() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, _watch = await _idle(probe, clock=clock)
    warden._state = WardenState.ACTIVE  # A bee is running.

    await watch_tick(warden)

    assert probe.calls == []


async def test_an_assignment_waiting_for_its_grant_pauses_watching() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, _watch = await _idle(probe, clock=clock)
    assignment = make_assignment(clock=clock)
    warden._pending[str(assignment.grant_id)] = assignment  # Parked: the grant has not come yet.

    await watch_tick(warden)

    assert probe.calls == []
    warden._pending.clear()
    await watch_tick(warden)
    assert len(probe.calls) == 1  # Back to idle: watching resumes.


async def test_watching_resumes_with_one_sample_not_a_burst() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, watch = await _idle(probe, clock=clock)
    await watch_tick(warden)
    warden._state = WardenState.ACTIVE
    clock.advance(10 * _INTERVAL)  # A long task.
    warden._state = WardenState.WATCH

    await watch_tick(warden)
    await watch_tick(warden)

    assert len(probe.calls) == 2  # One catch-up, not ten.
    assert watch.schedule.next_sample_at == clock.now() + timedelta(seconds=_INTERVAL)


async def test_a_lost_cell_link_pauses_watching() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, _watch = await _idle(probe, clock=clock)
    warden._cell_lost = True

    await watch_tick(warden)

    assert probe.calls == []


async def test_a_guard_set_without_the_watch_capability_switches_watching_off() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, _watch = await _idle(probe, clock=clock)
    warden._ceiling = CapabilitySet.empty()  # What `[guard] deny = ["watch:*"]` leaves.

    await watch_tick(warden)

    assert probe.calls == []


async def test_the_wardens_session_is_lent_to_a_probe_that_looks_through_it() -> None:
    clock = FakeClock()
    lent: list[CellSession | None] = []
    probe = StubProbe(_sample(clock))
    watch = dataclasses.replace(
        make_watch_deps(probe, interval_s=_INTERVAL), bind_session=lent.append
    )
    warden, _queen, _watch = await _idle(probe, watch=watch, clock=clock)

    await watch_tick(warden)

    assert lent == [warden._session] and warden._session is not None


async def test_a_probe_that_fails_costs_that_sample_only_and_is_retried_at_the_next_interval() -> (
    None
):
    clock = FakeClock()
    probe = StubProbe(OSError("the device went away"), _sample(clock))
    warden, _queen, watch = await _idle(probe, clock=clock)

    await watch_tick(warden)  # Fails inside the observer: logged, not raised.
    assert watch.schedule.next_sample_at == clock.now() + timedelta(seconds=_INTERVAL)
    await watch_tick(warden)
    assert len(probe.calls) == 1  # Not hammered at every tick.

    clock.advance(_INTERVAL)
    await watch_tick(warden)
    assert len(probe.calls) == 2


async def test_an_error_the_hive_raises_on_purpose_never_ends_the_wardens_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hivemind.wardens.watch import observer

    async def boom(self: object, level: object) -> bool:
        raise WaggleError("the store refused")

    monkeypatch.setattr(observer.WatchObserver, "sample", boom)
    clock = FakeClock()
    warden, _queen, watch = await _idle(StubProbe(_sample(clock)), clock=clock)

    await watch_tick(warden)  # Logged by type; the Warden carries on.

    assert watch.schedule.next_sample_at == clock.now() + timedelta(seconds=_INTERVAL)


# ──────────────────────────────────────────────────────────────────────────────
# Patrolling
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_first_patrol_is_one_interval_after_the_first_tick_and_then_every_interval() -> (
    None
):
    clock = FakeClock()
    probe = StubProbe(_sample(clock, share=0.9))
    replies = (json.dumps({"choice": "NOTHING", "reason": "Quiet."}),) * 2
    warden, _queen, watch = await _idle(probe, replies=replies, clock=clock)
    started = clock.now()

    await watch_tick(warden)
    assert watch.schedule.next_patrol_at == started + timedelta(seconds=_PATROL)
    assert await warden._deps.trail.query(TrailQuery(kind="warden.patrol")) == ()

    for _ in range(int(_PATROL / _INTERVAL)):
        clock.advance(_INTERVAL)
        await watch_tick(warden)
    (first,) = await warden._deps.trail.query(TrailQuery(kind="warden.patrol"))
    assert first.payload["outcome"] == "NOTHING"
    assert watch.schedule.next_patrol_at == clock.now() + timedelta(seconds=_PATROL)


async def test_a_patrol_that_decides_to_alarm_reaches_the_queen_with_counts_only() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock, name="MINER-9", share=0.95))
    reply = json.dumps({"choice": "ALARM", "reason": "MINER-9 pins the CPU."})
    warden, queen_end, _watch = await _idle(probe, replies=(reply,), clock=clock)
    await watch_tick(warden)

    clock.advance(_PATROL)
    await watch_tick(warden)

    alarm = await queen_end.wait_for_alarm()
    assert alarm.kind is AlarmKind.PATROL_ANOMALY
    assert "MINER-9" not in alarm.detail + alarm.reason


async def test_a_patrol_with_no_model_is_skipped_and_the_next_one_covers_its_observations() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    warden, _queen, watch = await _idle(probe, replies=(), clock=clock)  # Nothing scripted.
    await watch_tick(warden)
    clock.advance(_PATROL)

    await watch_tick(warden)

    (event,) = await warden._deps.trail.query(TrailQuery(kind="warden.patrol"))
    assert event.payload["outcome"] == "SKIPPED"
    assert watch.schedule.patrolled_until is None
    assert watch.schedule.next_patrol_at == clock.now() + timedelta(seconds=_PATROL)


async def test_a_restarted_warden_carries_on_from_the_last_patrol_the_trail_remembers() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    reply = json.dumps({"choice": "NOTHING", "reason": "Quiet."})
    warden, _queen, _watch = await _idle(probe, replies=(reply,), clock=clock)
    await watch_tick(warden)
    clock.advance(_PATROL)
    await watch_tick(warden)
    patrolled_at = clock.now()
    clock.advance(30.0)

    await warden.stop()  # The first process ends and gives its lease back.
    # The same Warden id and the same stores, with a fresh schedule: a process restart.
    fresh = make_watch_deps(probe, interval_s=_INTERVAL, patrol_interval_s=_PATROL)
    reborn = Warden(warden._warden_id, dataclasses.replace(warden._deps, watch=fresh))
    await reborn.start()
    await ticks.assign.settle_after_tick(reborn)
    await watch_tick(reborn)

    assert fresh.schedule.is_seeded is True
    assert fresh.schedule.next_patrol_at == patrolled_at + timedelta(seconds=_PATROL)
    assert fresh.schedule.patrolled_until == patrolled_at


# ──────────────────────────────────────────────────────────────────────────────
# Wired into the Warden's own run loop
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_wardens_run_loop_watches_only_while_it_is_idle() -> None:
    clock = FakeClock()
    probe = StubProbe(_sample(clock))
    watch = make_watch_deps(probe, interval_s=5.0, patrol_interval_s=10_000.0)

    async def hang(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await ctx.clock.sleep(1_000_000.0)  # A bee that works for the whole test.
        raise AssertionError("the bee should never wake")

    deps, queen_end, warden_id = make_warden_deps(
        clock,
        heartbeat_interval_s=1.0,
        watch=watch,
        worker_factory=lambda role: ScriptedWorker(hang, role=role),
    )
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    async def settle() -> None:
        for _ in range(30):
            await asyncio.sleep(0)

    await settle()
    for _ in range(6):  # Idle: a sample at the first tick, and the next five seconds later.
        clock.advance(1.1)
        await settle()
    idle_samples = len(probe.calls)

    assignment = make_assignment(clock=clock)
    await queen_end.send(assignment)
    await queen_end.send(make_grant_issued(clock, assignment))
    await settle()
    for _ in range(10):  # Busy: the same ten seconds again, with a bee on the Cell.
        clock.advance(1.1)
        await settle()

    assert idle_samples == 2
    assert warden.state is WardenState.ACTIVE
    assert len(probe.calls) == idle_samples
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
