"""Build valid hivemind.memory test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about, so a test that only cares about one field writes
`make_pin(clearance=HoneyClearance.C2)` rather than filling in every field by hand.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/memory and packages/hivemind/tests/contracts/
    test_memory_store_contract.py.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic.
    - Every builder's result passes the model's own validators with no further overrides needed.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.memory.handoff for Decision, Handoff.
    - hivemind.memory.pins for Pin, PinSource.
    - hivemind.memory.notes for Note.
    - hivemind.memory.episodes for EpisodeRecord.
    - hivemind.memory.hot_state for Principal, TokenBudget, TriggerEvent, TaskSummary,
      AlarmSummary, QuestionSummary, DecisionSummary.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.memory import (
    Decision,
    EpisodeRecord,
    Handoff,
    Note,
    Pin,
    PinSource,
)
from hivemind.memory.hot_state import (
    AlarmSummary,
    DecisionSummary,
    Principal,
    QuestionSummary,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
)
from waggle.clock import Clock, FakeClock
from waggle.ids import new_alarm_id, new_event_id, new_message_id, new_task_id

__all__ = [
    "make_alarm_summary",
    "make_decision_summary",
    "make_episode",
    "make_handoff",
    "make_note",
    "make_pin",
    "make_principal",
    "make_question_summary",
    "make_task_summary",
    "make_token_budget",
    "make_trigger_event",
]


def make_pin(clock: Clock | None = None, **overrides: object) -> Pin:
    """Build a valid, C1 RUNTIME Pin.

    Args:
        clock: Source of the id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Pin.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_event_id(active_clock),
        "text": "Always run the test suite before committing.",
        "clearance": HoneyClearance.C1,
        "source": PinSource.RUNTIME,
        "created_at": active_clock.now(),
    }
    fields.update(overrides)
    return Pin(**fields)


def make_note(clock: Clock | None = None, **overrides: object) -> Note:
    """Build a valid, C1 Note from a plain test author.

    Args:
        clock: Source of the id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Note.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_event_id(active_clock),
        "author": "worker_test",
        "text": "The config file lives under scratch/config.toml.",
        "clearance": HoneyClearance.C1,
        "written_at": active_clock.now(),
    }
    fields.update(overrides)
    return Note(**fields)


def make_handoff(clock: Clock | None = None, **overrides: object) -> Handoff:
    """Build a valid, C1 Handoff with one decision and one next step.

    Args:
        clock: Reserved for signature parity with the other builders; unused, since no Handoff
            field carries an id or a timestamp of its own.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Handoff.
    """
    fields: dict[str, object] = {
        "goal": "Finish wiring the new endpoint.",
        "progress": "The route is registered; the handler still returns a stub response.",
        "decisions": (
            Decision(what="Used the existing router.", why="Avoids a second dispatch path."),
        ),
        "tried_and_failed": (),
        "constraints": (),
        "open_threads": (),
        "next_steps": ("Implement the handler body.",),
        "do_not_redo": (),
        "pinned_facts": (),
        "notes": "",
        "clearance": HoneyClearance.C1,
        "written_by": "worker_test",
        "task_id": None,
    }
    fields.update(overrides)
    return Handoff(**fields)


def make_trigger_event(**overrides: object) -> TriggerEvent:
    """Build a valid, C1 TriggerEvent.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TriggerEvent.
    """
    fields: dict[str, object] = {
        "kind": "test.trigger",
        "summary": "A test event triggered this episode.",
        "payload_ref": None,
        "clearance": HoneyClearance.C1,
    }
    fields.update(overrides)
    return TriggerEvent(**fields)


def make_episode(clock: Clock | None = None, **overrides: object) -> EpisodeRecord:
    """Build a valid, C1 autopilot EpisodeRecord.

    Args:
        clock: Source of the id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated EpisodeRecord.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_event_id(active_clock),
        "principal": "warden_test",
        "slot": ModelSlot.WARDEN,
        "trigger": make_trigger_event(),
        "prompt_ref": None,
        "reasoning_summary": None,
        "decision": "Retry the failed step once more.",
        "action": "Respawned the worker.",
        "at": active_clock.now(),
        "clearance": HoneyClearance.C1,
        "usage": None,
        "is_autopilot": True,
    }
    fields.update(overrides)
    return EpisodeRecord(**fields)


def make_principal(**overrides: object) -> Principal:
    """Build a valid, C1 Principal on the WORKER slot.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated Principal.
    """
    fields: dict[str, object] = {
        "id": "worker_test",
        "slot": ModelSlot.WORKER,
        "clearance": HoneyClearance.C1,
        "role": "drone",
    }
    fields.update(overrides)
    return Principal(**fields)


def make_token_budget(**overrides: object) -> TokenBudget:
    """Build a valid TokenBudget: a generous window with a small output reserve.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TokenBudget.
    """
    fields: dict[str, object] = {"max_input_tokens": 8_000, "output_reserve": 1_000}
    fields.update(overrides)
    return TokenBudget(**fields)


def make_task_summary(clock: Clock | None = None, **overrides: object) -> TaskSummary:
    """Build a valid, C1 TaskSummary.

    Args:
        clock: Source of the id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TaskSummary.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_task_id(active_clock),
        "title": "Write the report",
        "status": "RUNNING",
        "objective": "Summarise last week's findings for the team.",
        "updated_at": active_clock.now(),
        "clearance": HoneyClearance.C1,
    }
    fields.update(overrides)
    return TaskSummary(**fields)


def make_alarm_summary(clock: Clock | None = None, **overrides: object) -> AlarmSummary:
    """Build a valid, C1 AlarmSummary with no task link.

    Args:
        clock: Source of the id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated AlarmSummary.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_alarm_id(active_clock),
        "kind": "WORKER_FAILED",
        "severity": "WARNING",
        "detail": "The tool call raised an exception.",
        "attempts": 1,
        "task_id": None,
        "clearance": HoneyClearance.C1,
        "raised_at": active_clock.now(),
    }
    fields.update(overrides)
    return AlarmSummary(**fields)


def make_question_summary(clock: Clock | None = None, **overrides: object) -> QuestionSummary:
    """Build a valid, C1 QuestionSummary with no options.

    Args:
        clock: Source of the id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated QuestionSummary.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_message_id(active_clock),
        "task_id": new_task_id(active_clock),
        "text": "Which environment should this deploy to?",
        "options": (),
        "clearance": HoneyClearance.C1,
        "asked_at": active_clock.now(),
    }
    fields.update(overrides)
    return QuestionSummary(**fields)


def make_decision_summary(clock: Clock | None = None, **overrides: object) -> DecisionSummary:
    """Build a valid, C1 DecisionSummary.

    Args:
        clock: Source of the episode id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated DecisionSummary.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "episode_id": new_event_id(active_clock),
        "at": active_clock.now(),
        "decision": "Retry the failed step once more.",
        "action": "Respawned the worker.",
        "clearance": HoneyClearance.C1,
    }
    fields.update(overrides)
    return DecisionSummary(**fields)
