"""Tests for hivemind.entrance.enrol.deps.goals: the no-op goal ledger.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/deps/goals.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.deps.goals for the module under test.
"""

from __future__ import annotations

from hivemind.entrance.enrol import GoalLedger, NullGoalLedger
from waggle.ids import DeviceId, TaskId


async def test_the_no_op_ledger_knows_no_goals_and_cancels_none() -> None:
    goals: GoalLedger = NullGoalLedger()

    assert await goals.open_goals(DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")) == ()
    assert await goals.cancel_goals([TaskId("task_01M221E4C10R4XDPNQNRX85AAA")], "revoked") == ()
