"""Tests for hivemind.wardens.autopilot.table: the Warden's deterministic dispatch table.

Fits into the Hive:
    Mirrors src/hivemind/wardens/autopilot/table.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.autopilot.table for the module under test.
    - .claude/roadmap.md step 3.19's own dispatch map for the table this module implements.
"""

from __future__ import annotations

import pytest
from builders.supervision import make_inbox_item, make_policy

from hivemind.supervision import AlarmKind as PolicyAlarmKind
from hivemind.supervision import PolicyAction, PolicyRule
from hivemind.supervision.attendant import InboxItem, InboxKind
from hivemind.wardens.autopilot import SubBeeView, WardenAction, decide
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.ids import (
    new_alarm_id,
    new_cell_id,
    new_grant_id,
    new_message_id,
    new_task_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages import AlarmSeverity
from waggle.messages.forage import GrantIssued
from waggle.messages.labels import AccuracyBar, Postcondition, PostconditionKind, Tempo
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    Answer,
    AnswerSource,
    ContextTelemetry,
    Heartbeat,
    Intervene,
    InterventionAction,
    Question,
)
from waggle.messages.supervision import WorkerState as WireWorkerState
from waggle.messages.task import (
    TaskAssign,
    TaskCancel,
    TaskOutcome,
    TaskPause,
    TaskProgress,
    TaskResult,
    TaskResume,
    TaskStage,
    WorkerRole,
)

_CLOCK = FakeClock()
_SUB_BEE = SubBeeView(state=WorkerState.RUNNING)


def _assignment() -> TaskAssign:
    task_id = new_task_id(_CLOCK)
    return TaskAssign(
        task_id=task_id,
        goal_id=task_id,
        cell_id=new_cell_id(_CLOCK),
        role=WorkerRole.DRONE,
        slot="WORKER",
        objective="Do the thing.",
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="out.txt", argv=(), expected=None
            ),
        ),
        tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        clearance=WireHoneyClearance.C1,
        grant_id=new_grant_id(_CLOCK),
        attempt=1,
        resume_from=None,
        reason="test",
    )


def _grant() -> GrantIssued:
    return GrantIssued(
        grant_id=new_grant_id(_CLOCK),
        holder=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        task_id=None,
        revision=0,
        allowed=(),
        seats=(),
        token_budget=1_000,
        spend_budget=1.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=_CLOCK.now(),
        reason="test",
    )


def _task_result(outcome: TaskOutcome, checked_by: object = None) -> TaskResult:
    return TaskResult(
        task_id=new_task_id(_CLOCK),
        attempt=1,
        outcome=outcome,
        summary="done",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=checked_by,
        handoff=None,
        spend=0.0,
        reason="test",
    )


def _alarm(kind: AlarmKind = AlarmKind.WORKER_FAILED, attempts: int = 0) -> AlarmRaised:
    return AlarmRaised(
        alarm_id=new_alarm_id(_CLOCK),
        kind=kind,
        severity=AlarmSeverity.WARNING,
        origin=new_worker_id(_CLOCK),
        attempts=attempts,
        raised_at=_CLOCK.now(),
        context=AlarmContext(
            task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="something failed",
        clearance=WireHoneyClearance.C1,
        reason="cannot resolve",
    )


def _item(payload: object, kind: InboxKind = InboxKind.WAGGLE_MESSAGE) -> InboxItem:
    return make_inbox_item(kind=kind, clock=_CLOCK, payload=payload, payload_kind="test.payload")


def test_decide_maps_task_assign_to_spawn() -> None:
    assert decide(_item(_assignment()), None, make_policy()) is WardenAction.SPAWN


def test_decide_maps_grant_issued_to_record() -> None:
    assert decide(_item(_grant()), None, make_policy()) is WardenAction.RECORD


def test_decide_maps_claimed_task_result_to_accept() -> None:
    result = _task_result(TaskOutcome.CLAIMED)
    assert decide(_item(result), _SUB_BEE, make_policy()) is WardenAction.ACCEPT


def test_decide_maps_non_claimed_task_result_to_record() -> None:
    result = _task_result(TaskOutcome.SUCCEEDED, checked_by=new_warden_id(_CLOCK))
    assert decide(_item(result), _SUB_BEE, make_policy()) is WardenAction.RECORD


def test_decide_maps_question_to_forward_question() -> None:
    question = Question(
        question_id=new_message_id(_CLOCK),
        task_id=new_task_id(_CLOCK),
        asked_by=new_worker_id(_CLOCK),
        text="what now?",
        options=(),
        clearance=WireHoneyClearance.C1,
        asked_at=_CLOCK.now(),
    )
    assert decide(_item(question), None, make_policy()) is WardenAction.FORWARD_QUESTION


def test_decide_maps_answer_to_forward_answer() -> None:
    answer = Answer(
        question_id=new_message_id(_CLOCK),
        task_id=new_task_id(_CLOCK),
        text="go ahead",
        chosen_option=None,
        source=AnswerSource.QUEEN,
        clearance=WireHoneyClearance.C1,
    )
    assert decide(_item(answer), None, make_policy()) is WardenAction.FORWARD_ANSWER


@pytest.mark.parametrize(
    "payload",
    [
        TaskCancel(task_id=new_task_id(_CLOCK), grace_s=1.0, reason="stop"),
        TaskPause(task_id=new_task_id(_CLOCK), reason="pause"),
        TaskResume(
            task_id=new_task_id(_CLOCK), attempt=1, resume_from=None, slot=None, reason="go"
        ),
        Intervene(
            action=InterventionAction.COMPACT,
            subject=None,
            task_id=None,
            slot=None,
            alarm_id=None,
            reason="compact",
        ),
    ],
)
def test_decide_maps_every_control_message_to_forward_control(payload: object) -> None:
    assert decide(_item(payload), None, make_policy()) is WardenAction.FORWARD_CONTROL


def test_decide_maps_task_progress_to_record() -> None:
    progress = TaskProgress(
        task_id=new_task_id(_CLOCK),
        attempt=1,
        stage=TaskStage.WORKING,
        summary="working",
        clearance=WireHoneyClearance.C1,
        fraction_done=None,
        handoff=None,
    )
    assert decide(_item(progress), _SUB_BEE, make_policy()) is WardenAction.RECORD


def test_decide_maps_heartbeat_to_record() -> None:
    heartbeat = Heartbeat(
        telemetry=ContextTelemetry(
            tokens_used=0, context_window=1, goal="", last_actions=(), blockers=(), spend=0.0
        ),
        task_id=None,
        worker_state=WireWorkerState.RUNNING,
        warden_state=None,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )
    assert decide(_item(heartbeat), _SUB_BEE, make_policy()) is WardenAction.RECORD


def test_decide_returns_needs_judgement_for_an_unrecognised_payload() -> None:
    assert decide(_item(object()), None, make_policy()) is WardenAction.NEEDS_JUDGEMENT


@pytest.mark.parametrize(
    ("policy_action", "expected"),
    [
        (PolicyAction.RETRY, WardenAction.RETRY),
        (PolicyAction.RESPAWN, WardenAction.RETRY),
        (PolicyAction.REBIND, WardenAction.REBIND),
        (PolicyAction.TAKEOVER, WardenAction.ESCALATE),
        (PolicyAction.ESCALATE, WardenAction.ESCALATE),
        (PolicyAction.CANCEL, WardenAction.CANCEL_TASK),
    ],
)
def test_decide_maps_every_policy_action_for_an_alarm(
    policy_action: PolicyAction, expected: WardenAction
) -> None:
    kind = AlarmKind.WORKER_FAILED
    policy = make_policy(
        rules=(
            PolicyRule(kind=PolicyAlarmKind.WORKER_FAILED, min_attempts=1, action=policy_action),
        ),
        default=PolicyAction.ESCALATE,
    )
    alarm = _alarm(kind=kind, attempts=1)

    assert decide(_item(alarm), _SUB_BEE, policy) is expected


def test_decide_falls_back_to_the_policys_default_for_a_fresh_alarm() -> None:
    policy = make_policy(rules=(), default=PolicyAction.ESCALATE)
    alarm = _alarm(attempts=0)

    assert decide(_item(alarm), None, policy) is WardenAction.ESCALATE
