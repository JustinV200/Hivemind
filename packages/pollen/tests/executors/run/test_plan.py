"""Test ExecPlan, the resolved and checked exec that the run machinery is handed.

By the time a ``session.exec`` request reaches the machinery that runs it, it has been read, its
working directory resolved against the lease's scratch directory and its capabilities checked.
What is left is a plain value: the program, where and with what environment, the limits it runs
under and who is waiting for its output. A run must never change that value under its own feet,
so the plan is frozen, and a changed copy is a different plan.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.run.plan.

Key invariants:
    - A plan cannot be modified after it is made.
    - Two plans with the same fields are equal.

See Also:
    - docs/waggle/gateway-contract.md section 11.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from pollen.executors.run.plan import ExecPlan
from waggle.clock import FakeClock
from waggle.ids import new_lease_id, new_message_id

CLOCK = FakeClock()


def _plan() -> ExecPlan:
    """A plan for a short program in a scratch directory."""
    scratch = Path("/scratch/lease")
    return ExecPlan(
        request_id=new_message_id(CLOCK),
        lease_id=new_lease_id(CLOCK),
        requester="warden",
        argv=("echo", "hello"),
        cwd=scratch,
        env={"KEY": "value"},
        timeout_s=5.0,
        has_stdin=False,
        max_output_bytes=100,
        scratch_root=scratch,
        quota_bytes=1_000,
    )


def test_a_plan_cannot_be_changed_once_it_is_made() -> None:
    plan = _plan()
    with pytest.raises(FrozenInstanceError):
        plan.__setattr__("argv", ("rm", "-rf", "/"))
    assert plan.argv == ("echo", "hello")


def test_a_changed_copy_is_another_plan_and_the_original_is_untouched() -> None:
    plan = _plan()
    longer = replace(plan, timeout_s=50.0)
    assert (plan.timeout_s, longer.timeout_s) == (5.0, 50.0)
    assert replace(plan) == plan and longer != plan
