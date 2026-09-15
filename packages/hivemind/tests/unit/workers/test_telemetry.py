"""Tests for hivemind.workers.telemetry.TelemetryTracker's rollback-to-Alarm threshold.

Fits into the Hive:
    Mirrors src/hivemind/workers/telemetry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.telemetry for the module under test.
"""

from __future__ import annotations

from hivemind.workers.telemetry import ROLLBACKS_BEFORE_ALARM, TelemetryTracker
from waggle.messages.supervision import AlarmKind


def test_rollbacks_below_the_threshold_queue_no_alarm() -> None:
    tracker = TelemetryTracker(context_window=10_000)

    queued = [
        tracker.note_rollback(AlarmKind.POSTCONDITION_FAILED, "did not hold")
        for _ in range(ROLLBACKS_BEFORE_ALARM - 1)
    ]

    # A failed command or two is ordinary work the role reads from its tool result; retiring the
    # bee for it (the Warden's RETRY row) threw away a context that was about to correct itself.
    assert queued == [False] * (ROLLBACKS_BEFORE_ALARM - 1)
    assert tracker.take_pending_alarms() == ()


def test_the_threshold_th_rollback_queues_one_postcondition_failed_alarm() -> None:
    tracker = TelemetryTracker(context_window=10_000)
    for _ in range(ROLLBACKS_BEFORE_ALARM - 1):
        tracker.note_rollback(AlarmKind.POSTCONDITION_FAILED, "did not hold")

    queued = tracker.note_rollback(AlarmKind.POSTCONDITION_FAILED, "still does not hold")

    assert queued is True
    pending = tracker.take_pending_alarms()
    assert len(pending) == 1
    assert pending[0].kind is AlarmKind.POSTCONDITION_FAILED
    assert pending[0].detail == "still does not hold"
