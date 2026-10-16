"""Test the pure resource-limit plan and the fuzz per-call timeout.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/limits.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/limits.py for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.royal_jelly.quarantine_comb.limits import (
    ADDRESS_SPACE_FACTOR,
    CPU_GRACE_S,
    FSIZE_BYTES,
    MAX_FUZZ_CALL_S,
    MIN_FUZZ_CALL_S,
    PROCESS_HEADROOM,
    THREAD_STACK_BYTES,
    Baseline,
    address_space_cap,
    fuzz_call_timeout,
    rlimit_plan,
)
from hivemind.royal_jelly.spec import QuarantineLimits

_MIB = 1024 * 1024


def test_rlimit_plan_adds_the_budget_to_the_baseline() -> None:
    limits = QuarantineLimits(cpu_s=10, memory_bytes=100 * _MIB)
    baseline = Baseline(cpu_s=1.5, address_space_bytes=300 * _MIB, task_count=200)

    plan = rlimit_plan(limits, baseline).limits

    assert plan["RLIMIT_CPU"] == (11 + CPU_GRACE_S, 11 + CPU_GRACE_S)
    expected_as = (
        300 * _MIB + 100 * _MIB * ADDRESS_SPACE_FACTOR + PROCESS_HEADROOM * THREAD_STACK_BYTES
    )
    assert plan["RLIMIT_AS"] == (expected_as, expected_as)
    assert plan["RLIMIT_FSIZE"] == (FSIZE_BYTES, FSIZE_BYTES)
    assert plan["RLIMIT_NPROC"] == (200 + PROCESS_HEADROOM, 200 + PROCESS_HEADROOM)


def test_rlimit_plan_skips_what_the_platform_could_not_measure() -> None:
    baseline = Baseline(cpu_s=0.0, address_space_bytes=0, task_count=0)

    plan = rlimit_plan(QuarantineLimits(), baseline).limits

    assert set(plan) == {"RLIMIT_CPU", "RLIMIT_FSIZE"}


def test_rlimit_plan_never_caps_below_the_baseline() -> None:
    baseline = Baseline(cpu_s=5.0, address_space_bytes=900 * _MIB, task_count=10)

    plan = rlimit_plan(QuarantineLimits(cpu_s=1, memory_bytes=16 * _MIB), baseline).limits

    assert plan["RLIMIT_CPU"][0] > 5
    assert plan["RLIMIT_AS"][0] > 900 * _MIB


def test_the_address_space_cap_leaves_room_for_reservations_the_rss_gate_never_sees() -> None:
    limits = QuarantineLimits(memory_bytes=16 * _MIB)  # The smallest budget allowed.
    baseline = Baseline(cpu_s=0.0, address_space_bytes=200 * _MIB, task_count=1)

    cap = address_space_cap(limits, baseline)

    # An honest tool at its full resident budget that also starts every thread the plan allows
    # (each reserving a stack) and maps a scratch file still sits under the cap ...
    honest_vsz = (
        baseline.address_space_bytes
        + limits.memory_bytes
        + PROCESS_HEADROOM * THREAD_STACK_BYTES
        + FSIZE_BYTES
    )
    assert honest_vsz <= cap
    # ... while the cap stays a real backstop: nowhere near the whole machine's memory.
    assert cap <= baseline.address_space_bytes + 1024 * _MIB
    assert rlimit_plan(limits, baseline).limits["RLIMIT_AS"] == (cap, cap)


@pytest.mark.parametrize(
    ("wall_s", "cases", "expected"),
    [(120, 32, 3.75), (10, 100, MIN_FUZZ_CALL_S), (3_600, 1, MAX_FUZZ_CALL_S), (60, 2, 30.0)],
)
def test_fuzz_call_timeout_shares_the_wall_budget_within_bounds(
    wall_s: float, cases: int, expected: float
) -> None:
    assert fuzz_call_timeout(QuarantineLimits(wall_s=wall_s, fuzz_cases=cases)) == expected
