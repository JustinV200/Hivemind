"""Tests for waggle.messages.capping.action: ProposedAction and ActionKind.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins ActionKind's members, and for
    ProposedAction construction of every kind, the JSON round trip, the rejection of an extra
    field, every per-field bound, and both validators spec section 8.9 names (the field
    matching the kind, and the total character cap) in both directions.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.capping.action for the module under test.
    - test_proposals.py for the family's EXAMPLES, which carry one ProposedAction.
    - docs/waggle/spec.md section 8.9 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.messages.base import MAX_PATH_CHARS
from waggle.messages.capping.action import (
    MAX_ACTION_CHARS,
    MAX_COMMAND_ITEM_CHARS,
    MAX_COMMAND_ITEMS,
    MAX_DIFF_CHARS,
    MAX_PATHS,
    MAX_STEP_CHARS,
    MAX_STEPS,
    MAX_SUMMARY_CHARS,
    ActionKind,
    ProposedAction,
)

SHA256 = "ab" * 32  # 32 digest bytes as 64 lowercase hex characters.
ACTION = ProposedAction(
    kind=ActionKind.DIFF,
    summary="Add the enrol route to the Landing Board.",
    diff="--- a/enrol.py\n+++ b/enrol.py\n@@ -1 +1,2 @@\n line\n+route\n",
    diff_sha256=None,
    command=(),
    cwd=None,
    paths=("src/hivemind/entrance/enrol.py",),
    steps=(),
)


def _rebuild(example: ProposedAction, **changes: object) -> ProposedAction:
    """Re-validate ``example`` with some fields replaced."""
    return ProposedAction.model_validate({**example.model_dump(), **changes})


def test_action_kind_members_and_values_match_the_spec() -> None:
    assert [member.name for member in ActionKind] == ["DIFF", "COMMAND", "ACTION_SEQUENCE"]
    assert all(member.value == member.name for member in ActionKind)


def test_proposed_action_round_trips_and_is_frozen() -> None:
    assert ProposedAction.model_validate(ACTION.model_dump(mode="json")) == ACTION
    with pytest.raises(ValidationError, match="frozen"):
        ACTION.summary = "changed"  # The assignment is the test.


def test_proposed_action_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(ACTION, hop_count=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"diff": None, "diff_sha256": SHA256},
        {"kind": "COMMAND", "diff": None, "command": ("pytest", "-q"), "cwd": "/scratch"},
        {"kind": "ACTION_SEQUENCE", "diff": None, "steps": ("Open settings", "Click save")},
    ],
    ids=["diff by digest", "command", "action sequence"],
)
def test_proposed_action_accepts_each_kind_with_its_own_field(changes: dict[str, object]) -> None:
    action = _rebuild(ACTION, **changes)

    assert action.kind is ActionKind(changes.get("kind", "DIFF"))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"diff_sha256": SHA256}, "exactly one of diff and diff_sha256"),
        ({"diff": None}, "exactly one of diff and diff_sha256"),
        ({"kind": "COMMAND", "command": ("ls",)}, "neither diff nor diff_sha256"),
        (
            {"kind": "COMMAND", "diff": None, "diff_sha256": SHA256, "command": ("ls",)},
            "neither diff nor diff_sha256",
        ),
        ({"kind": "COMMAND", "diff": None}, "command is non-empty exactly for a COMMAND"),
        ({"command": ("ls",)}, "command is non-empty exactly for a COMMAND"),
        ({"kind": "ACTION_SEQUENCE", "diff": None}, "steps is non-empty exactly for an ACTION"),
        ({"steps": ("Click save",)}, "steps is non-empty exactly for an ACTION"),
        (
            {"kind": "ACTION_SEQUENCE", "diff": None, "steps": ("s",), "command": ("ls",)},
            "command is non-empty exactly for a COMMAND",
        ),
    ],
)
def test_proposed_action_requires_exactly_the_field_of_its_kind(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(ACTION, **changes)


def test_proposed_action_bounds_its_text_in_total() -> None:
    # 64 arguments of 4096 characters reach the cap exactly on their own; one more character
    # anywhere else tips it over, even though every field is within its own bound.
    at_cap = _rebuild(
        ACTION,
        kind="COMMAND",
        diff=None,
        summary="",
        paths=(),
        command=("x" * MAX_COMMAND_ITEM_CHARS,) * MAX_COMMAND_ITEMS,
    )

    assert MAX_COMMAND_ITEMS * MAX_COMMAND_ITEM_CHARS == MAX_ACTION_CHARS
    assert at_cap.summary == ""
    with pytest.raises(ValidationError, match=f"at most {MAX_ACTION_CHARS} characters"):
        _rebuild(at_cap, summary="x")


def test_proposed_action_counts_diff_paths_and_steps_towards_the_cap() -> None:
    # A diff at its own cap plus paths at theirs is over the total, though each is within bounds.
    with pytest.raises(ValidationError, match=f"at most {MAX_ACTION_CHARS} characters"):
        _rebuild(
            ACTION, diff="x" * MAX_DIFF_CHARS, paths=("y" * MAX_PATH_CHARS,) * (MAX_PATHS // 2 + 1)
        )
    with pytest.raises(ValidationError, match=f"at most {MAX_ACTION_CHARS} characters"):
        _rebuild(
            ACTION,
            kind="ACTION_SEQUENCE",
            diff=None,
            steps=("s" * MAX_STEP_CHARS,) * MAX_STEPS,
            paths=("y" * MAX_PATH_CHARS,) * MAX_PATHS,
        )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"summary": "x" * (MAX_SUMMARY_CHARS + 1)}, f"at most {MAX_SUMMARY_CHARS}"),
        ({"diff": "x" * (MAX_DIFF_CHARS + 1)}, f"at most {MAX_DIFF_CHARS}"),
        ({"diff": None, "diff_sha256": SHA256.upper()}, "pattern"),
        ({"diff": None, "diff_sha256": SHA256[:-1]}, "pattern"),
        ({"cwd": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"paths": ("p",) * (MAX_PATHS + 1)}, f"at most {MAX_PATHS}"),
        ({"paths": ("x" * (MAX_PATH_CHARS + 1),)}, f"at most {MAX_PATH_CHARS}"),
        (
            {"kind": "COMMAND", "diff": None, "command": ("x",) * (MAX_COMMAND_ITEMS + 1)},
            f"at most {MAX_COMMAND_ITEMS}",
        ),
        (
            {"kind": "COMMAND", "diff": None, "command": ("x" * (MAX_COMMAND_ITEM_CHARS + 1),)},
            f"at most {MAX_COMMAND_ITEM_CHARS}",
        ),
        (
            {"kind": "ACTION_SEQUENCE", "diff": None, "steps": ("s",) * (MAX_STEPS + 1)},
            f"at most {MAX_STEPS}",
        ),
        (
            {"kind": "ACTION_SEQUENCE", "diff": None, "steps": ("x" * (MAX_STEP_CHARS + 1),)},
            f"at most {MAX_STEP_CHARS}",
        ),
        ({"kind": "PATCH"}, "kind"),
    ],
)
def test_proposed_action_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(ACTION, **changes)


def test_proposed_action_accepts_a_diff_at_its_cap_with_a_cwd() -> None:
    wide = _rebuild(ACTION, diff="x" * MAX_DIFF_CHARS, cwd="/scratch/lease")

    assert len(wide.diff or "") == MAX_DIFF_CHARS
    assert wide.cwd == "/scratch/lease"
