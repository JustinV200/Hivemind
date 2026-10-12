"""Test the runner's usage helpers: meter, counting writer, limit application and deadline.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/usage.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/usage.py for the module under test.
"""

from __future__ import annotations

import resource
import signal
import time

import pytest

from hivemind.royal_jelly.quarantine_comb.limits import RlimitPlan
from hivemind.royal_jelly.quarantine_comb.runner.usage import (
    CountingWriter,
    DeadlineExceededError,
    Meter,
    apply_limits,
    arm_deadline,
    disarm_deadline,
    take_baseline,
)


def test_the_counting_writer_counts_bytes_and_keeps_a_tail() -> None:
    writer = CountingWriter()

    writer.write("héllo")
    writer.write("x" * 10_000)

    assert writer.bytes_written == len("héllo".encode()) + 10_000
    assert len(writer.tail) == 4_000
    assert not writer.isatty()
    writer.flush()


def test_the_meter_reports_net_figures_and_never_negative() -> None:
    earlier = Meter(cpu_s=1.0, peak_rss_bytes=100, wall_s=10.0)
    later = Meter(cpu_s=3.5, peak_rss_bytes=90, wall_s=12.0)

    section = later.since(earlier, output_bytes=7, timed_out_phase="fuzz")

    assert section.cpu_s == 2.5
    assert section.peak_rss_bytes == 0
    assert section.wall_s == 2.0
    assert (section.output_bytes, section.timed_out_phase) == (7, "fuzz")


def test_the_baseline_reads_this_process() -> None:
    baseline = take_baseline()

    assert baseline.cpu_s > 0
    assert baseline.address_space_bytes >= 0
    assert baseline.process_count >= 0


def test_apply_limits_sets_known_limits_and_skips_unknown_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, pair: applied.append((which, pair)))
    monkeypatch.setattr(resource, "getrlimit", lambda which: (0, resource.RLIM_INFINITY))
    plan = RlimitPlan(
        limits={"RLIMIT_CPU": (30, 30), "RLIMIT_NOT_A_THING": (1, 1), "RLIMIT_AS": (0, 0)}
    )

    apply_limits(plan)

    assert applied == [(resource.RLIMIT_CPU, (30, 30))]


def test_apply_limits_never_raises_when_the_platform_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(which: int, pair: tuple[int, int]) -> None:
        raise ValueError("not allowed")

    monkeypatch.setattr(resource, "setrlimit", refuse)

    apply_limits(RlimitPlan(limits={"RLIMIT_CPU": (30, 30)}))


def test_apply_limits_does_not_raise_a_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[tuple[int, int]] = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, pair: applied.append(pair))
    monkeypatch.setattr(resource, "getrlimit", lambda which: (5, 20))

    apply_limits(RlimitPlan(limits={"RLIMIT_CPU": (30, 100)}))

    assert applied == [(20, 20)]


def test_the_deadline_interrupts_a_blocking_sleep() -> None:
    previous = signal.getsignal(signal.SIGALRM)
    try:
        assert arm_deadline(0.05)
        with pytest.raises(DeadlineExceededError):
            time.sleep(5)
    finally:
        disarm_deadline()
        signal.signal(signal.SIGALRM, previous)


def test_a_disarmed_deadline_never_fires() -> None:
    previous = signal.getsignal(signal.SIGALRM)
    try:
        arm_deadline(0.05)
        disarm_deadline()
        time.sleep(0.15)
    finally:
        signal.signal(signal.SIGALRM, previous)
