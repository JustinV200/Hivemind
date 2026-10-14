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
    CPU_GRACE_S,
    FSIZE_BYTES,
    MAX_FUZZ_CALL_S,
    MIN_FUZZ_CALL_S,
    PROCESS_HEADROOM,
    Baseline,
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
    assert plan["RLIMIT_AS"] == (400 * _MIB, 400 * _MIB)
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


@pytest.mark.parametrize(
    ("wall_s", "cases", "expected"),
    [(120, 32, 3.75), (10, 100, MIN_FUZZ_CALL_S), (3_600, 1, MAX_FUZZ_CALL_S), (60, 2, 30.0)],
)
def test_fuzz_call_timeout_shares_the_wall_budget_within_bounds(
    wall_s: float, cases: int, expected: float
) -> None:
    assert fuzz_call_timeout(QuarantineLimits(wall_s=wall_s, fuzz_cases=cases)) == expected
