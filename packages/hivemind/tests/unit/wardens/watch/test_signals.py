"""Tests for hivemind.wardens.watch.signals: the Patrol's pure pre-filter.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/signals.py (codingrules section 3). The rules are checked
    on small hand-built windows; hypothesis then checks what must hold of any window at all: the
    filter never raises, never exceeds its bounds, and a higher threshold never finds more.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.signals for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from builders.watch import GIB, make_observation, make_process, make_resources
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.wardens.watch.signals import (
    MAX_PER_KIND,
    RESTART_LOOP_MIN_STARTS,
    SignalKind,
    WatchSignal,
    find_signals,
)
from waggle.messages.swarm import Observation, ProcessSample

_T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
_CPU, _DISK = 0.5, 0.95


def _at(minutes: int) -> datetime:
    """The instant `minutes` after the window opened."""
    return _T0 + timedelta(minutes=minutes)


def _obs(minutes: int, *processes: ProcessSample, disk_free: int | None = None) -> Observation:
    """An Observation `minutes` into the window; a disk figure adds a resource sample."""
    resources = make_resources(disk_free_bytes=disk_free) if disk_free is not None else None
    return make_observation(_at(minutes), processes=processes, resources=resources)


def _kinds(signals: tuple[WatchSignal, ...]) -> list[SignalKind]:
    """The kinds found, in order."""
    return [signal.kind for signal in signals]


# ──────────────────────────────────────────────────────────────────────────────
# New process over the CPU share
# ──────────────────────────────────────────────────────────────────────────────


def test_a_process_that_started_in_the_window_and_uses_the_cpu_is_a_signal() -> None:
    quiet = _obs(0, make_process(1, "init", 0.01, started_at=_T0 - timedelta(days=3)))
    hog = make_process(4242, "miner", 0.91, started_at=_at(3))

    (signal,) = find_signals([quiet, _obs(5, hog)], _CPU, _DISK)

    assert (signal.kind, signal.subject, signal.pid) == (SignalKind.NEW_PROCESS_CPU, "miner", 4242)
    assert (signal.value, signal.at) == (0.91, _at(5))


def test_a_process_that_was_already_running_when_the_window_opened_is_not_new() -> None:
    old_hog = make_process(7, "backup", 0.95, started_at=_T0 - timedelta(hours=2))

    assert find_signals([_obs(0, old_hog), _obs(5, old_hog)], _CPU, _DISK) == ()


def test_a_new_process_under_the_threshold_is_not_a_signal() -> None:
    mild = make_process(8, "build", 0.49, started_at=_at(1))

    assert find_signals([_obs(0), _obs(5, mild)], _CPU, _DISK) == ()


def test_a_process_at_exactly_the_threshold_is_a_signal() -> None:
    edge = make_process(9, "edge", 0.5, started_at=_at(1))

    assert _kinds(find_signals([_obs(0), _obs(5, edge)], _CPU, _DISK)) == [
        SignalKind.NEW_PROCESS_CPU
    ]


def test_the_peak_share_over_the_window_is_the_one_reported() -> None:
    def at(share: float) -> ProcessSample:
        return make_process(5, "job", share, started_at=_at(1))

    window = [_obs(0), _obs(5, at(0.6)), _obs(10, at(0.9)), _obs(15, at(0.7))]

    (signal,) = find_signals(window, _CPU, _DISK)

    assert (signal.value, signal.at) == (0.9, _at(10))


def test_without_start_times_a_process_first_seen_after_the_first_sample_is_new() -> None:
    baseline = make_process(1, "daemon", 0.9)
    arrival = make_process(2, "newcomer", 0.8)

    window = [_obs(0, baseline), _obs(5, baseline, arrival)]

    (signal,) = find_signals(window, _CPU, _DISK)
    assert signal.subject == "newcomer"  # The first sample is the baseline: nothing in it is new.


def test_new_processes_are_listed_worst_first_and_capped() -> None:
    hogs = [
        make_process(100 + n, f"p{n:02d}", 0.5 + n / 100, started_at=_at(1))
        for n in range(MAX_PER_KIND + 4)
    ]

    signals = find_signals([_obs(0), _obs(5, *hogs)], _CPU, _DISK)

    assert len(signals) == MAX_PER_KIND
    assert [s.value for s in signals] == sorted((s.value for s in signals), reverse=True)
    assert signals[0].subject == f"p{MAX_PER_KIND + 3:02d}"


# ──────────────────────────────────────────────────────────────────────────────
# The disk
# ──────────────────────────────────────────────────────────────────────────────


def test_a_disk_filled_past_the_alarm_is_a_signal() -> None:
    (signal,) = find_signals([_obs(0, disk_free=2 * GIB)], _CPU, _DISK)  # 98% of 100 GiB used.

    assert (signal.kind, signal.subject) == (SignalKind.DISK_FILLING, "disk")
    assert signal.value == 0.98 and signal.pid is None


def test_a_disk_below_the_alarm_is_not() -> None:
    assert find_signals([_obs(0, disk_free=10 * GIB)], _CPU, _DISK) == ()


def test_only_the_newest_measurement_of_the_disk_counts() -> None:
    fuller_then_freed = [_obs(0, disk_free=1 * GIB), _obs(5, disk_free=60 * GIB)]
    filling = [_obs(0, disk_free=60 * GIB), _obs(5, disk_free=1 * GIB)]

    assert find_signals(fuller_then_freed, _CPU, _DISK) == ()
    assert _kinds(find_signals(filling, _CPU, _DISK)) == [SignalKind.DISK_FILLING]


def test_the_newest_sample_that_measured_the_disk_is_used_when_the_last_did_not() -> None:
    window = [_obs(0, disk_free=1 * GIB), _obs(5)]  # The last sample carries no resources.

    assert _kinds(find_signals(window, _CPU, _DISK)) == [SignalKind.DISK_FILLING]


# ──────────────────────────────────────────────────────────────────────────────
# Restart loops
# ──────────────────────────────────────────────────────────────────────────────


def _service(pid: int, minute: int) -> ProcessSample:
    """One incarnation of a service called `flaky`, started at `minute`."""
    return make_process(pid, "flaky", 0.01, started_at=_at(minute))


def test_one_name_seen_as_three_processes_in_turn_is_a_restart_loop() -> None:
    window = [_obs(1, _service(11, 0)), _obs(6, _service(12, 5)), _obs(11, _service(13, 10))]

    (signal,) = find_signals(window, _CPU, _DISK)

    assert (signal.kind, signal.subject, signal.value) == (SignalKind.RESTART_LOOP, "flaky", 3.0)
    assert (signal.pid, signal.at) == (13, _at(11))


def test_two_starts_are_not_yet_a_loop() -> None:
    assert RESTART_LOOP_MIN_STARTS == 3
    assert find_signals([_obs(1, _service(11, 0)), _obs(6, _service(12, 5))], _CPU, _DISK) == ()


def test_a_name_that_runs_several_processes_at_once_is_not_a_restart_loop() -> None:
    tabs = [
        _obs(n, *(make_process(100 + k, "browser", 0.0, started_at=_at(k)) for k in range(4)))
        for n in (1, 6, 11)
    ]

    assert find_signals(tabs, _CPU, _DISK) == ()


def test_a_name_that_once_ran_two_at_once_is_never_called_a_loop() -> None:
    window = [
        _obs(1, _service(11, 0)),
        _obs(6, _service(12, 5), _service(14, 5)),  # Two at once, once.
        _obs(11, _service(13, 10)),
    ]

    assert find_signals(window, _CPU, _DISK) == ()


def test_the_same_process_seen_again_is_one_start() -> None:
    steady = make_process(11, "flaky", 0.0, started_at=_at(0))

    assert find_signals([_obs(1, steady), _obs(6, steady), _obs(11, steady)], _CPU, _DISK) == ()


# ──────────────────────────────────────────────────────────────────────────────
# The whole filter
# ──────────────────────────────────────────────────────────────────────────────


def test_an_empty_window_has_no_signals() -> None:
    assert find_signals([], _CPU, _DISK) == ()


def test_signals_come_new_processes_first_then_the_disk_then_restart_loops() -> None:
    hog = make_process(5, "hog", 0.9, started_at=_at(1))
    window = [
        _obs(0, _service(11, -5), disk_free=1 * GIB),
        _obs(5, hog, _service(12, 3)),
        _obs(10, _service(13, 8), disk_free=1 * GIB),
    ]

    assert _kinds(find_signals(window, _CPU, _DISK)) == [
        SignalKind.NEW_PROCESS_CPU,
        SignalKind.DISK_FILLING,
        SignalKind.RESTART_LOOP,
    ]


_instants = st.datetimes(
    min_value=datetime(2026, 5, 1), max_value=datetime(2026, 5, 2), timezones=st.just(UTC)
)
_processes = st.lists(
    st.builds(
        make_process,
        pid=st.integers(1, 30),
        name=st.sampled_from(["a", "b", "c", "d"]),
        cpu_share=st.floats(0, 1),
        started_at=st.one_of(st.none(), _instants),
    ),
    max_size=8,
)
_windows = st.lists(
    st.tuples(_processes, st.one_of(st.none(), st.integers(0, 100 * GIB))), min_size=0, max_size=6
).map(
    lambda rows: [
        make_observation(
            _T0 + timedelta(minutes=5 * n),
            processes=processes,
            resources=None if free is None else make_resources(disk_free_bytes=free),
        )
        for n, (processes, free) in enumerate(rows)
    ]
)


@settings(max_examples=150, deadline=None)
@given(window=_windows, cpu=st.floats(0.01, 1), disk=st.floats(0.01, 1))
def test_the_filter_never_raises_and_stays_within_its_bounds(
    window: list[Observation], cpu: float, disk: float
) -> None:
    signals = find_signals(window, cpu, disk)

    assert len(signals) <= 3 * MAX_PER_KIND
    for kind in SignalKind:
        assert sum(1 for s in signals if s.kind is kind) <= MAX_PER_KIND
    assert all(0 <= s.value <= 1 for s in signals if s.kind is not SignalKind.RESTART_LOOP)
    assert find_signals(window, cpu, disk) == signals  # The same window gives the same answer.


@settings(max_examples=150, deadline=None)
@given(window=_windows, low=st.floats(0.01, 1), high=st.floats(0.01, 1))
def test_raising_a_threshold_never_adds_a_signal(
    window: list[Observation], low: float, high: float
) -> None:
    low, high = sorted((low, high))

    def count(kind: SignalKind, signals: tuple[WatchSignal, ...]) -> int:
        return sum(1 for s in signals if s.kind is kind)

    easy, strict = find_signals(window, low, low), find_signals(window, high, high)

    for kind in (SignalKind.NEW_PROCESS_CPU, SignalKind.DISK_FILLING):
        assert count(kind, strict) <= count(kind, easy)
    assert count(SignalKind.RESTART_LOOP, strict) == count(SignalKind.RESTART_LOOP, easy)
