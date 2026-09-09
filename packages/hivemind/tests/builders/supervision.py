"""Build valid hivemind.supervision test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about, so a test that only cares about one field writes
`make_alarm(state=AlarmState.HANDLING)` rather than filling in nine unrelated fields by hand.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/supervision.

Key invariants:
    - make_alarm and make_inbox_item take an optional `clock: Clock` (default a fresh FakeClock)
      so every id and timestamp they produce is deterministic across a test run.
    - Every builder's result passes the model's own validators with no further overrides needed.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.supervision.alarm for Alarm, AlarmKind, AlarmSeverity, AlarmState.
    - hivemind.supervision.attendant for InboxItem, InboxKind.
    - hivemind.supervision.policy for EscalationPolicy, PolicyRule, PolicyAction.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.supervision import (
    Alarm,
    AlarmKind,
    AlarmSeverity,
    AlarmState,
    EscalationPolicy,
    PolicyAction,
    PolicyRule,
)
from hivemind.supervision.attendant import InboxItem, InboxKind
from waggle.clock import Clock, FakeClock
from waggle.ids import new_alarm_id, new_worker_id
from waggle.messages.supervision import AlarmContext, ContextTelemetry

__all__ = ["make_alarm", "make_inbox_item", "make_policy", "make_telemetry"]


def make_alarm(
    state: AlarmState = AlarmState.RAISED, clock: Clock | None = None, **overrides: object
) -> Alarm:
    """Build a valid Alarm in `state`, raised by a plain WORKER_FAILED with no context references.

    Args:
        state: The Alarm's AlarmState; RAISED by default.
        clock: Source of every minted id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `state` itself.

    Returns:
        A validated Alarm.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_alarm_id(active_clock),
        "kind": AlarmKind.WORKER_FAILED,
        "severity": AlarmSeverity.WARNING,
        "origin": new_worker_id(active_clock),
        "attempts": 0,
        "context": AlarmContext(
            task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        "detail": "The tool call raised an exception.",
        "clearance": HoneyClearance.C1,
        "raised_at": active_clock.now(),
        "state": state,
    }
    fields.update(overrides)
    return Alarm(**fields)


def make_telemetry(**overrides: object) -> ContextTelemetry:
    """Build a valid ContextTelemetry: a bee a third of the way through its window, no blockers.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated ContextTelemetry.
    """
    fields: dict[str, object] = {
        "tokens_used": 2_048,
        "context_window": 8_192,
        "goal": "Do the thing.",
        "last_actions": (),
        "blockers": (),
        "spend": 0.0,
    }
    fields.update(overrides)
    return ContextTelemetry(**fields)


def make_inbox_item(
    kind: InboxKind = InboxKind.WAGGLE_MESSAGE, clock: Clock | None = None, **overrides: object
) -> InboxItem:
    """Build a valid InboxItem of `kind`, timestamped now, with no task link or latency budget.

    Args:
        kind: The item's InboxKind; WAGGLE_MESSAGE by default.
        clock: Source of the id and the received_at timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `kind` itself.

    Returns:
        A validated InboxItem. `severity` defaults to AlarmSeverity.WARNING when `kind` is ALARM,
        None otherwise, so a caller building an ALARM item need not set it by hand.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": "inbox_item_1",
        "kind": kind,
        "received_at": active_clock.now(),
        "principal": "test-principal",
        "severity": AlarmSeverity.WARNING if kind is InboxKind.ALARM else None,
        "task_id": None,
        "latency_budget_s": None,
        "payload_kind": "test.payload",
        "payload": None,
    }
    fields.update(overrides)
    return InboxItem(**fields)


def make_policy(**overrides: object) -> EscalationPolicy:
    """Build a valid EscalationPolicy: a couple of WORKER_FAILED rows plus one wildcard row.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated EscalationPolicy.
    """
    fields: dict[str, object] = {
        "rules": (
            PolicyRule(kind=AlarmKind.WORKER_FAILED, min_attempts=1, action=PolicyAction.RETRY),
            PolicyRule(kind=AlarmKind.WORKER_FAILED, min_attempts=2, action=PolicyAction.ESCALATE),
            PolicyRule(kind=None, min_attempts=1, action=PolicyAction.RESPAWN),
        ),
        "default": PolicyAction.ESCALATE,
    }
    fields.update(overrides)
    return EscalationPolicy(**fields)
