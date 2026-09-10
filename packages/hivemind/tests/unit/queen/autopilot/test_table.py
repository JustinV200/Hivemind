"""Tests for hivemind.queen.autopilot.table.decide: the Queen's deterministic dispatch table.

Fits into the Hive:
    Mirrors src/hivemind/queen/autopilot/table.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.autopilot.table for the module under test.
"""

from __future__ import annotations

from builders.supervision import make_inbox_item, make_telemetry
from builders.tasks import make_task

from hivemind.brood_chamber import TaskStatus
from hivemind.queen.autopilot import QueenAction, decide
from hivemind.supervision import AlarmKind as HiveAlarmKind
from hivemind.supervision import EscalationPolicy, PolicyAction, PolicyRule
from hivemind.supervision.attendant import InboxKind
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    Answer,
    AnswerSource,
    Heartbeat,
    Question,
    WardenState,
)
from waggle.messages.task import ArtifactRef, TaskOutcome, TaskResult

_DEFAULT_LIMIT = 3


def _make_alarm(clock: FakeClock, *, kind: AlarmKind, task_id: object | None = None) -> AlarmRaised:
    return AlarmRaised(
        alarm_id=new_alarm_id(clock),
        kind=kind,
        severity=AlarmSeverity.WARNING,
        origin=new_worker_id(clock),
        attempts=0,
        raised_at=clock.now(),
        context=AlarmContext(
            task_id=task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="Something went wrong.",
        clearance=HoneyClearance.C1,
        reason="Cannot resolve locally.",
    )


def _make_result(clock: FakeClock, *, outcome: TaskOutcome) -> TaskResult:
    return TaskResult(
        task_id=new_task_id(clock),
        attempt=1,
        outcome=outcome,
        summary="Some outcome.",
        clearance=HoneyClearance.C1,
        artifacts=(ArtifactRef(path="scratch/out.txt", size_bytes=1, sha256="a" * 64),),
        checked_by=new_warden_id(clock) if outcome is not TaskOutcome.CLAIMED else None,
        handoff=None,
        spend=0.0,
        reason="Done.",
    )


def test_heartbeat_is_recorded() -> None:
    clock = FakeClock()
    heartbeat = Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=heartbeat)

    action = decide(item, None, 0, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.RECORD


def test_task_result_succeeded_completes_the_task() -> None:
    clock = FakeClock()
    result = _make_result(clock, outcome=TaskOutcome.SUCCEEDED)
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=result)
    task = make_task(status=TaskStatus.RUNNING, clock=clock)

    action = decide(item, task, 1, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.COMPLETE_TASK


def test_task_result_failed_retries_below_the_limit() -> None:
    clock = FakeClock()
    result = _make_result(clock, outcome=TaskOutcome.FAILED)
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=result)
    task = make_task(status=TaskStatus.RUNNING, clock=clock)

    action = decide(item, task, 1, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.RETRY_TASK


def test_task_result_failed_fails_once_attempts_reach_the_limit() -> None:
    clock = FakeClock()
    result = _make_result(clock, outcome=TaskOutcome.FAILED)
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=result)
    task = make_task(status=TaskStatus.RUNNING, clock=clock)

    action = decide(item, task, 3, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.FAIL_TASK


def test_task_result_for_an_already_terminal_task_is_a_stale_record() -> None:
    clock = FakeClock()
    result = _make_result(clock, outcome=TaskOutcome.FAILED)
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=result)
    task = make_task(status=TaskStatus.SUCCEEDED, clock=clock)

    action = decide(item, task, 5, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.RECORD


def test_alarm_retry_action_maps_to_retry_task() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.WORKER_FAILED)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(
            PolicyRule(kind=HiveAlarmKind.WORKER_FAILED, min_attempts=1, action=PolicyAction.RETRY),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.RETRY_TASK


def test_alarm_respawn_action_also_maps_to_retry_task() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.WORKER_CRASHED)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(
            PolicyRule(
                kind=HiveAlarmKind.WORKER_CRASHED, min_attempts=1, action=PolicyAction.RESPAWN
            ),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.RETRY_TASK


def test_alarm_rebind_action_maps_to_rebind() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.PROVIDER_UNAVAILABLE)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(
            PolicyRule(
                kind=HiveAlarmKind.PROVIDER_UNAVAILABLE, min_attempts=1, action=PolicyAction.REBIND
            ),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.REBIND


def test_alarm_escalate_action_maps_to_escalate_to_human() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.GRANT_EXCEEDED)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(
            PolicyRule(
                kind=HiveAlarmKind.GRANT_EXCEEDED, min_attempts=1, action=PolicyAction.ESCALATE
            ),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.ESCALATE_TO_HUMAN


def test_alarm_cancel_action_maps_to_fail_task() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.QUOTA_EXCEEDED)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(
            PolicyRule(
                kind=HiveAlarmKind.QUOTA_EXCEEDED, min_attempts=1, action=PolicyAction.CANCEL
            ),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.FAIL_TASK


def test_alarm_takeover_action_maps_to_escalate_to_human() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.OTHER)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(PolicyRule(kind=HiveAlarmKind.OTHER, min_attempts=1, action=PolicyAction.TAKEOVER),),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.ESCALATE_TO_HUMAN


def test_alarm_escalates_once_attempts_reach_the_limit_even_if_policy_would_retry() -> None:
    clock = FakeClock()
    alarm = _make_alarm(clock, kind=AlarmKind.WORKER_CRASHED)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    # Without the limit, attempts=2 would only clear the min_attempts=1 RESPAWN row.
    policy = EscalationPolicy(
        rules=(
            PolicyRule(
                kind=HiveAlarmKind.WORKER_CRASHED, min_attempts=1, action=PolicyAction.RESPAWN
            ),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, None, 2, policy, 2)

    assert action is QueenAction.ESCALATE_TO_HUMAN


def test_alarm_for_an_already_terminal_task_is_a_stale_record() -> None:
    clock = FakeClock()
    task = make_task(status=TaskStatus.FAILED, clock=clock)
    alarm = _make_alarm(clock, kind=AlarmKind.WORKER_FAILED, task_id=task.id)
    item = make_inbox_item(InboxKind.ALARM, clock=clock, payload=alarm)
    policy = EscalationPolicy(
        rules=(
            PolicyRule(kind=HiveAlarmKind.WORKER_FAILED, min_attempts=1, action=PolicyAction.RETRY),
        ),
        default=PolicyAction.ESCALATE,
    )

    action = decide(item, task, 1, policy, _DEFAULT_LIMIT)

    assert action is QueenAction.RECORD


def test_question_blocks_on_question() -> None:
    clock = FakeClock()
    question = Question(
        question_id="msg_" + "0" * 26,
        task_id=new_task_id(clock),
        asked_by=new_worker_id(clock),
        text="Which environment?",
        options=(),
        clearance=HoneyClearance.C1,
        asked_at=clock.now(),
    )
    item = make_inbox_item(InboxKind.QUESTION, clock=clock, payload=question)

    action = decide(item, None, 0, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.BLOCK_ON_QUESTION


def test_answer_routes_the_answer() -> None:
    clock = FakeClock()
    answer = Answer(
        question_id="msg_" + "0" * 26,
        task_id=new_task_id(clock),
        text="Use staging.",
        chosen_option=None,
        source=AnswerSource.QUEEN,
        clearance=HoneyClearance.C1,
    )
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=answer)

    action = decide(item, None, 0, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.ROUTE_ANSWER


def test_an_unrecognised_payload_needs_judgement() -> None:
    clock = FakeClock()
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, clock=clock, payload=object())

    action = decide(item, None, 0, EscalationPolicy(rules=(), default=PolicyAction.ESCALATE), 3)

    assert action is QueenAction.NEEDS_JUDGEMENT
