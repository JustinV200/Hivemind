"""Tests for hivemind.wardens.watch.observer: one read-only look at the Cell, kept as C2 Bee Bread.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/observer.py (codingrules section 3). A `StubProbe` stands
    for the probe where only what the observer does with an answer matters; the real
    `SessionWatchProbe` over a recording session stands for it where the claim is about the
    device (nothing but `exec` is ever called on it).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.observer for the module under test.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from builders.watch import (
    DEVICE_ID,
    FakeProcess,
    RecordingSession,
    ScriptedMachine,
    StubProbe,
    make_memory_context,
    make_observation,
    make_process,
    make_resources,
    make_watch_deps,
)
from pydantic import ValidationError

from hivemind.cell import AccessLevel, HoneyClearance, SessionClosedError
from hivemind.common.errors import HiveMindError
from hivemind.memory import MemoryContext
from hivemind.memory.bee_bread import BeeBreadEntryKind
from hivemind.pheromone import TrailQuery
from hivemind.wardens.watch import SessionWatchProbe, store
from hivemind.wardens.watch import observer as observer_module
from hivemind.wardens.watch.observer import WatchObserver, is_empty, permitted_kinds
from waggle.clock import FakeClock
from waggle.errors import WaggleError
from waggle.messages.swarm import FileChange, FileChangeKind, LogLine, Observation, ObservationKind

_ROOT = "/data/logs"
_SYSTEM = (ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE)


async def _kept(ctx: MemoryContext, clock: FakeClock) -> list[Observation]:
    """Every Observation kept so far."""
    window = timedelta(days=30)
    kept = await store.load_observations(ctx, clock.now() - window, clock.now() + window)
    return [item.observation for item in kept]


def _change(clock: FakeClock, seconds: float, path: str = "/r/f") -> FileChange:
    """A change to `path` `seconds` after the clock's now."""
    return FileChange(
        path=path,
        change=FileChangeKind.MODIFIED,
        at=clock.now() + timedelta(seconds=seconds),
        size_bytes=1,
    )


# ──────────────────────────────────────────────────────────────────────────────
# What may be asked for
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("level", list(AccessLevel))
def test_every_access_level_watches_the_same_four_things_when_roots_are_named(
    level: AccessLevel,
) -> None:
    assert permitted_kinds(level, True) == tuple(ObservationKind)


@pytest.mark.parametrize("level", list(AccessLevel))
def test_without_roots_only_the_process_list_and_resource_use_are_asked_for(
    level: AccessLevel,
) -> None:
    assert permitted_kinds(level, False) == _SYSTEM


def test_an_observation_with_nothing_in_it_is_empty() -> None:
    clock = FakeClock()

    assert is_empty(make_observation(clock.now())) is True
    assert is_empty(make_observation(clock.now(), resources=make_resources())) is False
    assert is_empty(make_observation(clock.now(), processes=[make_process()])) is False
    assert (
        is_empty(make_observation(clock.now(), log_lines=[LogLine(path="/a", line="x")])) is False
    )
    assert is_empty(make_observation(clock.now(), file_changes=[_change(clock, 0)])) is False


# ──────────────────────────────────────────────────────────────────────────────
# A sample
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_sample_is_kept_as_a_c2_bee_bread_entry() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    observation = make_observation(clock.now(), processes=[make_process(name="a", cpu_share=0.2)])
    watch = make_watch_deps(StubProbe(observation))

    kept = await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY)

    assert kept is True
    assert await _kept(ctx, clock) == [observation]
    (entry,) = await ctx.store.list_bee_bread_of_kind(
        BeeBreadEntryKind.WATCH_OBSERVATION,
        clock.now() - timedelta(seconds=1),
        clock.now() + timedelta(seconds=1),
        HoneyClearance.C2,
    )
    assert entry.clearance is HoneyClearance.C2


async def test_the_probe_is_asked_only_for_what_the_level_and_the_roots_allow() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    probe = StubProbe(make_observation(clock.now(), resources=make_resources()))

    await WatchObserver(make_watch_deps(probe), ctx).sample(AccessLevel.SCRATCH)
    await WatchObserver(make_watch_deps(probe, roots=(_ROOT,)), ctx).sample(AccessLevel.FULL)

    (no_roots, with_roots) = probe.calls
    assert no_roots == (_SYSTEM, (), None)
    assert with_roots == (tuple(ObservationKind), (_ROOT,), None)


async def test_samples_are_swept_past_the_retention_window() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    probe = StubProbe(make_observation(clock.now(), resources=make_resources()))
    watch = make_watch_deps(probe, retention_s=120.0)
    observer = WatchObserver(watch, ctx)
    await observer.sample(AccessLevel.READ_ONLY)

    clock.advance(121.0)
    await observer.sample(AccessLevel.READ_ONLY)

    assert len(await _kept(ctx, clock)) == 1  # The first has expired and was swept.


async def test_nothing_found_is_not_kept() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    watch = make_watch_deps(StubProbe(make_observation(clock.now())))

    kept = await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY)

    assert kept is False
    assert await _kept(ctx, clock) == []


# ──────────────────────────────────────────────────────────────────────────────
# The file-change cursor
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_next_sample_asks_for_changes_after_the_newest_one_seen() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    first = make_observation(
        clock.now(), file_changes=[_change(clock, -50, "/r/a"), _change(clock, -20, "/r/b")]
    )
    second = make_observation(clock.now(), file_changes=[_change(clock, -5, "/r/c")])
    probe = StubProbe(first, second)
    watch = make_watch_deps(probe, roots=(_ROOT,))
    observer = WatchObserver(watch, ctx)

    await observer.sample(AccessLevel.READ_ONLY)
    await observer.sample(AccessLevel.READ_ONLY)

    assert probe.calls[0][2] is None
    assert probe.calls[1][2] == clock.now() - timedelta(seconds=20)
    assert watch.schedule.file_cursor == clock.now() - timedelta(seconds=5)


async def test_a_file_stamped_in_the_future_cannot_move_the_cursor_past_the_present() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    sample = make_observation(clock.now(), file_changes=[_change(clock, 10**8)])
    watch = make_watch_deps(StubProbe(sample), roots=(_ROOT,))

    await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY)

    assert watch.schedule.file_cursor == sample.observed_at  # Not the year 2023.


async def test_the_cursor_only_moves_forward_and_stays_when_nothing_changed() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    newer = make_observation(clock.now(), file_changes=[_change(clock, -10)])
    older = make_observation(clock.now(), file_changes=[_change(clock, -40)])
    quiet = make_observation(clock.now(), resources=make_resources())
    watch = make_watch_deps(StubProbe(newer, older, quiet), roots=(_ROOT,))
    observer = WatchObserver(watch, ctx)

    for _ in range(3):
        await observer.sample(AccessLevel.READ_ONLY)

    assert watch.schedule.file_cursor == clock.now() - timedelta(seconds=10)


async def test_no_cursor_is_sent_when_file_changes_are_not_watched() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    probe = StubProbe(make_observation(clock.now(), resources=make_resources()))
    watch = make_watch_deps(probe)
    watch.schedule.file_cursor = clock.now()

    await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY)

    assert probe.calls[0][2] is None


# ──────────────────────────────────────────────────────────────────────────────
# When the look fails
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        HiveMindError("a device is away"),
        WaggleError("a frame was refused"),
        TimeoutError(),
        OSError("a socket broke"),
        SessionClosedError(Path("/scratch")),
    ],
)
async def test_a_failed_look_is_not_kept_and_not_raised(error: Exception) -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    watch = make_watch_deps(StubProbe(error))

    kept = await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY)

    assert kept is False
    assert await _kept(ctx, clock) == []


async def test_a_snapshot_that_breaks_its_own_bounds_is_a_failed_look() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    with pytest.raises(ValidationError) as caught:
        Observation.model_validate({"device_id": "x"})
    watch = make_watch_deps(StubProbe(caught.value))

    assert await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY) is False


async def test_a_bug_in_the_probe_is_not_swallowed() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    watch = make_watch_deps(StubProbe(TypeError("a bug")))

    with pytest.raises(TypeError):
        await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY)


async def test_a_probe_that_never_answers_is_given_up_on(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hung:
        async def observe(self, kinds: object, roots: object, since_at: object) -> Observation:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    monkeypatch.setattr(observer_module, "OBSERVE_TIMEOUT_S", 0.01)
    ctx, _trail = make_memory_context(FakeClock())

    kept = await WatchObserver(make_watch_deps(Hung()), ctx).sample(AccessLevel.READ_ONLY)

    assert kept is False


async def test_a_store_that_will_not_take_the_sample_is_a_failed_look(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", refuse)
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    watch = make_watch_deps(StubProbe(make_observation(clock.now(), resources=make_resources())))

    assert await WatchObserver(watch, ctx).sample(AccessLevel.READ_ONLY) is False


# ──────────────────────────────────────────────────────────────────────────────
# Read-only on a real probe
# ──────────────────────────────────────────────────────────────────────────────


async def test_sampling_through_a_session_never_calls_a_write_method_on_it() -> None:
    clock = FakeClock()
    ctx, trail = make_memory_context(clock)
    machine = ScriptedMachine(boot_epoch=clock.now().timestamp() - 1_000.0, uptime_s=1_000.0)
    machine.processes = [FakeProcess(1, "init", utime=10, started_s=1.0)]
    machine.files = {_ROOT: [(f"{_ROOT}/app.log", clock.now().timestamp() - 5, 20)]}
    machine.logs = {f"{_ROOT}/app.log": "a secret line\n"}
    session = RecordingSession(Path("/scratch"), clock, machine)
    probe = SessionWatchProbe(DEVICE_ID, clock)
    probe.use_session(session)
    watch = make_watch_deps(probe, roots=(_ROOT,))
    observer = WatchObserver(watch, ctx)

    assert await observer.sample(AccessLevel.READ_ONLY) is True
    assert await observer.sample(AccessLevel.FULL) is True

    assert set(session.calls) == {"exec"}
    (first, _second) = await _kept(ctx, clock)
    assert first.log_lines[0].line == "a secret line"
    # The trail records that samples were kept, never what was in them.
    events = await trail.query(TrailQuery())
    assert "secret" not in repr([event.payload for event in events])
