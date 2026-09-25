"""Every table keyed by PolicyAction or AlarmKind covers every member, and SECURITY always goes up.

`PolicyAction` is the Hive's own vocabulary, mirrored by no wire enum (ADR-0043), so nothing but a
test notices a member some dispatch table forgot: that would surface as a `KeyError` in a tick,
the first time a policy row named it. These tests walk the enums themselves, so a member added
without its rows fails here, for both tables that map one: the Warden's autopilot
(`hivemind.wardens.autopilot.table`) and the Queen's (`hivemind.queen.autopilot.table`). The
shipped escalation policy is walked the same way for every `AlarmKind`, and its SECURITY row
(roadmap steps 10.6 and 10.6c) is pinned: a security Alarm is never retried, respawned or rebound
at any level; a Warden sends it to the Queen, and she sends it on to the human.

Fits into the Hive:
    Mirrors src/hivemind/supervision/policy.py (codingrules section 3), across the two tables that
    consume it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.policy for PolicyAction and decide.
    - supervision/defaults/default-policy.toml for the shipped rows.
"""

from __future__ import annotations

import pytest
from builders.supervision import make_inbox_item

from hivemind.queen.autopilot import QueenAction
from hivemind.queen.autopilot import decide as queen_decide
from hivemind.supervision import (
    Alarm,
    AlarmKind,
    EscalationPolicy,
    PolicyAction,
    PolicyRule,
    load_policy,
)
from hivemind.supervision import decide as policy_decide
from hivemind.supervision.attendant import InboxItem, InboxKind
from hivemind.wardens.autopilot import SubBeeView, WardenAction
from hivemind.wardens.autopilot import decide as warden_decide
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_event_id, new_task_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmRaised
from waggle.messages.supervision import AlarmKind as WireAlarmKind

_CLOCK = FakeClock()
_SUB_BEE = SubBeeView(state=WorkerState.RUNNING, attempt=1)
_LIMIT = 3  # The Queen's attempt ceiling; kept above the attempt counts used here.
_ATTEMPTS = range(0, 6)  # Every attempt count a policy row could be reached at, and past them.


def _alarm(kind: WireAlarmKind, attempts: int = 1) -> AlarmRaised:
    """An Alarm about one bee's task, naming the trail event that best explains it."""
    return AlarmRaised(
        alarm_id=new_alarm_id(_CLOCK),
        kind=kind,
        severity=AlarmSeverity.WARNING,
        origin=new_worker_id(_CLOCK),
        attempts=attempts,
        raised_at=_CLOCK.now(),
        context=AlarmContext(
            task_id=new_task_id(_CLOCK),
            cell_id=None,
            worker_id=new_worker_id(_CLOCK),
            event_id=new_event_id(_CLOCK),
            handoff=None,
        ),
        detail="Something the policy keys on.",
        clearance=HoneyClearance.C1,
        reason="Cannot resolve it here.",
    )


def _item(alarm: AlarmRaised) -> InboxItem:
    return make_inbox_item(InboxKind.ALARM, clock=_CLOCK, payload=alarm, task_id=None)


def _one_row(action: PolicyAction) -> EscalationPolicy:
    """A policy whose one row answers OTHER with `action`."""
    return EscalationPolicy(
        rules=(PolicyRule(kind=AlarmKind.OTHER, min_attempts=1, action=action),),
        default=PolicyAction.ESCALATE,
    )


@pytest.mark.parametrize("action", list(PolicyAction), ids=lambda action: action.value)
def test_the_wardens_table_has_a_row_for_every_policy_action(action: PolicyAction) -> None:
    decided = warden_decide(_item(_alarm(WireAlarmKind.OTHER)), _SUB_BEE, _one_row(action))

    assert isinstance(decided, WardenAction)
    assert decided is not WardenAction.NEEDS_JUDGEMENT


@pytest.mark.parametrize("action", list(PolicyAction), ids=lambda action: action.value)
def test_the_queens_table_has_a_row_for_every_policy_action(action: PolicyAction) -> None:
    item = _item(_alarm(WireAlarmKind.OTHER))

    decided = queen_decide(item, None, 1, _one_row(action), _LIMIT)

    assert isinstance(decided, QueenAction)
    assert decided is not QueenAction.NEEDS_JUDGEMENT


def test_quarantine_maps_to_each_levels_own_lever() -> None:
    policy = _one_row(PolicyAction.QUARANTINE)
    item = _item(_alarm(WireAlarmKind.OTHER))

    assert warden_decide(item, _SUB_BEE, policy) is WardenAction.QUARANTINE
    # About no sub-bee this Warden supervises: nobody to quarantine, so the Queen decides.
    assert warden_decide(item, None, policy) is WardenAction.ESCALATE
    assert queen_decide(item, None, 1, policy, _LIMIT) is QueenAction.QUARANTINE_BEE


def test_isolate_is_the_queens_lever_and_a_warden_sends_it_up() -> None:
    policy = _one_row(PolicyAction.ISOLATE)
    item = _item(_alarm(WireAlarmKind.OTHER))

    # Roadmap step 10.6a: only the Queen isolates; a Warden that met the row escalates.
    assert queen_decide(item, None, 1, policy, _LIMIT) is QueenAction.ISOLATE_CELL
    assert warden_decide(item, _SUB_BEE, policy) is WardenAction.ESCALATE


@pytest.mark.parametrize("kind", list(AlarmKind), ids=lambda kind: kind.value)
def test_the_shipped_policy_decides_every_alarm_kind_at_every_attempt(kind: AlarmKind) -> None:
    shipped = load_policy()

    for attempts in _ATTEMPTS:
        alarm = Alarm.from_wire(_alarm(WireAlarmKind(kind.value), attempts))
        assert isinstance(policy_decide(shipped, alarm), PolicyAction)


def test_a_security_alarm_always_goes_up_never_retried_or_rebound() -> None:
    shipped = load_policy()

    for attempts in _ATTEMPTS:
        alarm = _alarm(WireAlarmKind.SECURITY, attempts)
        assert policy_decide(shipped, Alarm.from_wire(alarm)) is PolicyAction.ESCALATE
        view = SubBeeView(state=WorkerState.RUNNING, attempt=max(attempts, 1))
        assert warden_decide(_item(alarm), view, shipped) is WardenAction.ESCALATE
        decided = queen_decide(_item(alarm), None, attempts, shipped, _LIMIT)
        assert decided is QueenAction.ESCALATE_TO_HUMAN
