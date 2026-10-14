"""Tests for the retention an in-memory recording store may be given.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/exoskeleton/recorder/store.py
    (codingrules section 3). The store's protocol is held to one behaviour over both
    implementations by tests/contracts/test_recording_store_contract.py; what is tested here is
    only what the in-memory one adds, forgetting a stale recording as the next one opens, which
    is what lets the Warden of a colonized device (it runs for months) keep a window of
    recordings and not every one it ever made.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.exoskeleton.recorder.store for the module under test.
    - hivemind.cli.compose.exoskeleton.device_exoskeleton for the Warden that is given one.
"""

from __future__ import annotations

from datetime import timedelta

from builders.recordings import RECORDING_START, make_recorded_action, make_recording_info

from hivemind.exoskeleton.recorder import InMemoryRecordingStore, Retention
from waggle.clock import FakeClock

_DAY = timedelta(days=1)
_HOUR_S = 3600.0


async def _open_now(store: InMemoryRecordingStore, clock: FakeClock, recording_id: str) -> None:
    """Open a recording that starts at the clock's now and add one action made at that moment."""
    await store.open(make_recording_info(recording_id, started_at=clock.now()))
    await store.add(recording_id, make_recorded_action(at=clock.now()))


async def _ids(store: InMemoryRecordingStore) -> set[str]:
    """The ids of every recording the store holds."""
    return {info.recording_id for info in await store.recordings()}


async def test_a_store_with_a_retention_forgets_a_stale_recording_as_the_next_one_opens() -> None:
    clock = FakeClock(RECORDING_START)
    store = InMemoryRecordingStore(Retention(clock, _DAY))
    await _open_now(store, clock, "rec_old")

    clock.advance(30 * _HOUR_S)  # Past the day, by six hours.
    await _open_now(store, clock, "rec_new")

    # The old one had nothing in it newer than a day; the one that opened is newer than any cutoff.
    assert await _ids(store) == {"rec_new"}


async def test_it_keeps_a_recording_that_is_still_being_written_however_long_ago_it_started() -> (
    None
):
    clock = FakeClock(RECORDING_START)
    store = InMemoryRecordingStore(Retention(clock, _DAY))
    await _open_now(store, clock, "rec_long")
    clock.advance(40 * _HOUR_S)
    # An action finished forty hours in: the task is still at work, whatever time it started.
    await store.add("rec_long", make_recorded_action(at=clock.now()))

    clock.advance(_HOUR_S)
    await _open_now(store, clock, "rec_other")

    assert await _ids(store) == {"rec_long", "rec_other"}


async def test_it_forgets_nothing_until_a_recording_opens() -> None:
    clock = FakeClock(RECORDING_START)
    store = InMemoryRecordingStore(Retention(clock, _DAY))
    await _open_now(store, clock, "rec_old")

    clock.advance(60 * 24 * _HOUR_S)  # A Warden left idle for two months.

    # Nothing sweeps on a timer, so an idle Warden does no work and its last recording is still
    # there to be read until another one is made.
    assert await _ids(store) == {"rec_old"}


async def test_a_store_without_a_retention_keeps_every_recording() -> None:
    clock = FakeClock(RECORDING_START)
    store = InMemoryRecordingStore()
    await _open_now(store, clock, "rec_old")

    clock.advance(60 * 24 * _HOUR_S)
    await _open_now(store, clock, "rec_new")

    assert await _ids(store) == {"rec_old", "rec_new"}
