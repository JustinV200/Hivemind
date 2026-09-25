"""Tests for hivemind.guard.capabilities.capability_set: CapabilitySet and its narrowing methods.

Fits into the Hive:
    Mirrors src/hivemind/guard/capabilities/capability_set.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.capabilities.capability_set for the module under test.
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the tree".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.guard.capabilities.capability import Capability
from hivemind.guard.capabilities.capability_set import CapabilitySet
from hivemind.guard.errors import CapabilityWideningError, InvalidCapabilityError


def test_empty_has_no_members() -> None:
    empty = CapabilitySet.empty()

    assert len(empty) == 0
    assert list(empty) == []


def test_parse_builds_one_capability_per_distinct_string() -> None:
    capability_set = CapabilitySet.parse("net:*", "exec:*", "net:*")

    assert len(capability_set) == 2
    assert set(capability_set) == {Capability.parse("net:*"), Capability.parse("exec:*")}


def test_parse_rejects_a_malformed_string() -> None:
    with pytest.raises(InvalidCapabilityError):
        CapabilitySet.parse("net:*", "bogus")


def test_allows_true_when_a_member_matches() -> None:
    capability_set = CapabilitySet.parse("fs:write:/scratch/**", "observe")

    assert capability_set.allows(Capability.parse("fs:write:/scratch/out.txt")) is True
    assert capability_set.allows(Capability.parse("observe")) is True


def test_allows_false_when_no_member_matches() -> None:
    capability_set = CapabilitySet.parse("fs:read:**", "observe")

    assert capability_set.allows(Capability.parse("fs:write:/scratch/out.txt")) is False
    assert capability_set.allows(Capability.parse("observe:thoughts")) is False


def test_attenuate_returns_the_subset_unchanged_when_allowed() -> None:
    wide = CapabilitySet.parse("fs:write:/scratch/**", "net:*", "llm:*")
    narrow = CapabilitySet.parse("fs:write:/scratch/out.txt", "net:*.example.com", "llm:worker")

    assert wide.attenuate(narrow) is narrow


def test_attenuate_accepts_a_set_to_itself() -> None:
    # Every valid capability covers itself, so a set can always be handed on unchanged.
    capability_set = CapabilitySet.parse(
        "net:*.example.com", "net:10.0.0.0/8", "honey:clearance:c1", "spend:5", "observe"
    )

    assert capability_set.attenuate(capability_set) is capability_set


def test_attenuate_raises_naming_the_offending_capability() -> None:
    narrow = CapabilitySet.parse("fs:read:**")
    wider = CapabilitySet.parse("fs:read:**", "fs:write:/scratch/out.txt")

    with pytest.raises(CapabilityWideningError) as excinfo:
        narrow.attenuate(wider)

    assert excinfo.value.offending == "fs:write:/scratch/out.txt"


def test_issubset_true_when_every_capability_is_allowed() -> None:
    narrow = CapabilitySet.parse("fs:write:/scratch/out.txt", "honey:clearance:c0")
    wide = CapabilitySet.parse("fs:write:/scratch/**", "honey:clearance:c2")

    assert narrow.issubset(wide) is True


def test_issubset_false_when_a_capability_is_not_allowed() -> None:
    left = CapabilitySet.parse("fs:read:**", "net:*")
    right = CapabilitySet.parse("fs:read:**")

    assert left.issubset(right) is False


def test_as_strings_is_sorted_and_rebuilds_an_equal_set() -> None:
    capability_set = CapabilitySet.parse("tool:*", "observe", "fs:read:**", "tool:scope:cell")

    strings = capability_set.as_strings()

    assert strings == ("fs:read:**", "observe", "tool:*", "tool:scope:cell")
    assert CapabilitySet.parse(*strings) == capability_set


def test_has_no_union_or_other_widening_method() -> None:
    # codingrules section 15: widening is never a method. Checked on an instance: the class
    # itself answers `|` through its metaclass (that is how `int | str` builds a type union).
    capability_set = CapabilitySet.empty()

    for name in ("union", "__or__", "__ior__", "update", "add", "extend", "widen"):
        assert not hasattr(capability_set, name)


def test_is_frozen() -> None:
    capability_set = CapabilitySet.empty()

    with pytest.raises(ValidationError, match="frozen"):
        capability_set.capabilities = frozenset()  # type: ignore[misc]  # The assignment is the test.


def test_json_round_trips() -> None:
    original = CapabilitySet.parse("fs:read:**", "net:*", "observe", "cell:comb_shield:meadow")

    restored = CapabilitySet.model_validate_json(original.model_dump_json())

    assert restored == original
