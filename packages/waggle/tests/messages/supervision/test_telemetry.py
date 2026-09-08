"""Tests for waggle.messages.supervision.telemetry: the state enums and the three value models.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins WorkerState's and WardenState's members
    and wire values to spec section 8.3, and for ContextTelemetry, ChildTelemetry and
    CompactView construction, the JSON round trip, the rejection of an extra field, every bound
    the spec states, and CompactView's total-size validator in both directions.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.supervision.telemetry for the module under test.
    - test_oversight.py for the messages that carry these models.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import BaseModel, ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.supervision.telemetry import (
    MAX_ACTION_CHARS,
    MAX_BLOCKER_CHARS,
    MAX_BLOCKERS,
    MAX_GOAL_CHARS,
    MAX_LAST_ACTIONS,
    MAX_PROGRESS_CHARS,
    MAX_VIEW_CHARS,
    MAX_VIEW_ITEM_CHARS,
    MAX_VIEW_ITEMS,
    ChildTelemetry,
    CompactView,
    ContextTelemetry,
    WardenState,
    WorkerState,
)

CLOCK = FakeClock()
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
TELEMETRY = ContextTelemetry(
    tokens_used=12_000,
    context_window=200_000,
    goal="Summarise the release notes.",
    last_actions=("Read notes.md", "Drafted summary.md"),
    blockers=("Waiting on the version string.",),
    spend=0.12,
)
CHILD = ChildTelemetry(
    worker_id=WORKER_ID, task_id=TASK_ID, state=WorkerState.RUNNING, telemetry=TELEMETRY
)
VIEW = CompactView(
    goal="Summarise the release notes.",
    progress="The draft is written; the review is next.",
    decisions=("Kept the changelog order.",),
    open_threads=("Confirm the version string.",),
)


def _rebuild(model: BaseModel, **changes: object) -> BaseModel:
    """Re-validate ``model`` with some fields replaced."""
    return type(model).model_validate({**model.model_dump(), **changes})


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("enum_type", "names"),
    [
        (
            WorkerState,
            ["SPAWNED", "RUNNING", "HANDING_OFF", "PAUSED", "DONE", "FAILED", "KILLED"],
        ),
        (
            WardenState,
            ["STARTING", "ACTIVE", "WATCH", "OFFLINE", "CLUSTERED", "MIGRATING", "STOPPED"],
        ),
    ],
)
def test_enum_has_exactly_the_spec_members_with_values_equal_to_names(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


# ──────────────────────────────────────────────────────────────────────────────
# Every model
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", [TELEMETRY, CHILD, VIEW], ids=lambda model: type(model).__name__)
def test_telemetry_model_round_trips_is_frozen_and_rejects_an_extra_field(
    model: BaseModel,
) -> None:
    assert type(model).model_validate(model.model_dump(mode="json")) == model
    with pytest.raises(ValidationError, match="frozen"):
        model.goal = "changed"  # type: ignore[attr-defined]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(model, transcript="never")


# ──────────────────────────────────────────────────────────────────────────────
# ContextTelemetry and ChildTelemetry
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"tokens_used": -1}, "greater than or equal to 0"),
        ({"context_window": 0}, "greater than or equal to 1"),
        ({"goal": "g" * (MAX_GOAL_CHARS + 1)}, f"at most {MAX_GOAL_CHARS}"),
        ({"last_actions": ("a",) * (MAX_LAST_ACTIONS + 1)}, f"at most {MAX_LAST_ACTIONS}"),
        ({"last_actions": ("a" * (MAX_ACTION_CHARS + 1),)}, f"at most {MAX_ACTION_CHARS}"),
        ({"blockers": ("b",) * (MAX_BLOCKERS + 1)}, f"at most {MAX_BLOCKERS}"),
        ({"blockers": ("b" * (MAX_BLOCKER_CHARS + 1),)}, f"at most {MAX_BLOCKER_CHARS}"),
        ({"spend": -0.01}, "greater than or equal to 0"),
    ],
)
def test_context_telemetry_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(TELEMETRY, **changes)


def test_context_telemetry_accepts_every_bound_exactly_and_an_empty_context() -> None:
    full = _rebuild(
        TELEMETRY,
        goal="g" * MAX_GOAL_CHARS,
        last_actions=("a" * MAX_ACTION_CHARS,) * MAX_LAST_ACTIONS,
        blockers=("b" * MAX_BLOCKER_CHARS,) * MAX_BLOCKERS,
    )
    empty = _rebuild(
        TELEMETRY, tokens_used=0, context_window=1, goal="", last_actions=(), blockers=(), spend=0.0
    )

    assert isinstance(full, ContextTelemetry)
    assert len(full.last_actions) == MAX_LAST_ACTIONS
    assert isinstance(empty, ContextTelemetry)
    assert empty.tokens_used == 0


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"worker_id": TASK_ID}, "worker_"),
        ({"task_id": WORKER_ID}, "task_"),
        ({"state": "IDLE"}, "state"),
        ({"telemetry": {**TELEMETRY.model_dump(), "spend": -1.0}}, "greater than or equal to 0"),
    ],
)
def test_child_telemetry_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(CHILD, **changes)


def test_child_telemetry_may_have_no_task() -> None:
    idle = _rebuild(CHILD, task_id=None, state="SPAWNED")

    assert isinstance(idle, ChildTelemetry)
    assert idle.task_id is None
    assert idle.state is WorkerState.SPAWNED


# ──────────────────────────────────────────────────────────────────────────────
# CompactView
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"goal": "g" * (MAX_GOAL_CHARS + 1)}, f"at most {MAX_GOAL_CHARS}"),
        ({"progress": "p" * (MAX_PROGRESS_CHARS + 1)}, f"at most {MAX_PROGRESS_CHARS}"),
        ({"decisions": ("d",) * (MAX_VIEW_ITEMS + 1)}, f"at most {MAX_VIEW_ITEMS}"),
        ({"decisions": ("d" * (MAX_VIEW_ITEM_CHARS + 1),)}, f"at most {MAX_VIEW_ITEM_CHARS}"),
        ({"open_threads": ("t",) * (MAX_VIEW_ITEMS + 1)}, f"at most {MAX_VIEW_ITEMS}"),
        ({"open_threads": ("t" * (MAX_VIEW_ITEM_CHARS + 1),)}, f"at most {MAX_VIEW_ITEM_CHARS}"),
    ],
)
def test_compact_view_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(VIEW, **changes)


def test_compact_view_bounds_the_total_across_all_four_sections() -> None:
    # A full goal, a full progress and enough full decisions land exactly on the view cap while
    # every section stays within its own bound; one more character anywhere tips the total.
    decisions_at_bound = (
        MAX_VIEW_CHARS - MAX_GOAL_CHARS - MAX_PROGRESS_CHARS
    ) // MAX_VIEW_ITEM_CHARS
    assert 0 < decisions_at_bound < MAX_VIEW_ITEMS
    decisions = ("d" * MAX_VIEW_ITEM_CHARS,) * decisions_at_bound
    at_bound = {
        "goal": "g" * MAX_GOAL_CHARS,
        "progress": "p" * MAX_PROGRESS_CHARS,
        "decisions": decisions,
        "open_threads": (),
    }

    assert CompactView.model_validate(at_bound)
    with pytest.raises(ValidationError, match=f"more than the {MAX_VIEW_CHARS}"):
        CompactView.model_validate({**at_bound, "open_threads": ("t",)})
    with pytest.raises(ValidationError, match=f"more than the {MAX_VIEW_CHARS}"):
        CompactView.model_validate({**at_bound, "decisions": (*decisions, "d")})


def test_compact_view_total_counts_every_section_not_the_longest_one() -> None:
    lists_only = CompactView(
        goal="",
        progress="",
        decisions=("d" * MAX_VIEW_ITEM_CHARS,) * (MAX_VIEW_ITEMS // 2),
        open_threads=("t" * MAX_VIEW_ITEM_CHARS,) * (MAX_VIEW_ITEMS // 2),
    )

    assert len(lists_only.decisions) + len(lists_only.open_threads) == MAX_VIEW_ITEMS
    with pytest.raises(ValidationError, match=f"more than the {MAX_VIEW_CHARS}"):
        _rebuild(lists_only, goal="g")


def test_compact_view_may_be_empty() -> None:
    empty = CompactView(goal="", progress="", decisions=(), open_threads=())

    assert empty.model_dump(mode="json") == {
        "goal": "",
        "progress": "",
        "decisions": [],
        "open_threads": [],
    }
